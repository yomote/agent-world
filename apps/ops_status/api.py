import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .models import (
    FocusSummary,
    RuntimeCapacitySnapshot,
    StatusResponse,
    StatusSnapshot,
    StatusUpsertReceipt,
    StatusUpsertRequest,
    WorkItem,
)
from .store import (
    SnapshotConflictError,
    SnapshotStore,
    SnapshotStoreError,
    configured_store,
    status_response,
)


def reject_older_item(
    previous: WorkItem,
    incoming: WorkItem,
    *,
    allow_same_observation_transition: bool = False,
) -> None:
    if incoming.observed_at < previous.observed_at:
        raise HTTPException(status_code=409, detail=f"older observation for agent {incoming.agent}")
    observed_fields = {
        "observed_at",
        "latest_activity",
        "latest_activity_at",
        "current_action",
        "progress_summary",
        "instruction_summary",
        "summary_updated_at",
        "parent_relation",
        "parent_agent",
        "parent_source",
        "parent_observed_at",
    }
    newer_activity = incoming.latest_activity_at is not None and (
        previous.latest_activity_at is None
        or incoming.latest_activity_at > previous.latest_activity_at
    )
    if (
        incoming.observed_at == previous.observed_at
        and previous.model_dump(exclude=observed_fields)
        != incoming.model_dump(exclude=observed_fields)
        and not (allow_same_observation_transition and newer_activity)
    ):
        raise HTTPException(
            status_code=409, detail=f"ambiguous observation for agent {incoming.agent}"
        )

    previous_activity = (previous.latest_activity, previous.latest_activity_at)
    incoming_activity = (incoming.latest_activity, incoming.latest_activity_at)
    if previous_activity != incoming_activity and (
        incoming.latest_activity_at is None
        or (
            previous.latest_activity_at is not None
            and incoming.latest_activity_at <= previous.latest_activity_at
        )
    ):
        raise HTTPException(status_code=409, detail=f"older activity for agent {incoming.agent}")

    previous_summary = (
        previous.current_action,
        previous.progress_summary,
        previous.instruction_summary,
        previous.summary_updated_at,
    )
    incoming_summary = (
        incoming.current_action,
        incoming.progress_summary,
        incoming.instruction_summary,
        incoming.summary_updated_at,
    )
    if previous_summary != incoming_summary and (
        incoming.summary_updated_at is None
        or (
            previous.summary_updated_at is not None
            and incoming.summary_updated_at <= previous.summary_updated_at
        )
    ):
        raise HTTPException(status_code=409, detail=f"older summary for agent {incoming.agent}")

    previous_parent = (
        previous.parent_relation,
        previous.parent_agent,
        previous.parent_source,
        previous.parent_observed_at,
    )
    incoming_parent = (
        incoming.parent_relation,
        incoming.parent_agent,
        incoming.parent_source,
        incoming.parent_observed_at,
    )
    if previous_parent != incoming_parent and (
        incoming.parent_observed_at is None
        or (
            previous.parent_observed_at is not None
            and incoming.parent_observed_at <= previous.parent_observed_at
        )
    ):
        raise HTTPException(
            status_code=409, detail=f"older parent relation for agent {incoming.agent}"
        )


def reject_older_capacity(
    previous: RuntimeCapacitySnapshot, incoming: RuntimeCapacitySnapshot
) -> None:
    if incoming.observed_at < previous.observed_at:
        raise HTTPException(status_code=409, detail="older runtime capacity observation")
    if incoming.observed_at == previous.observed_at and incoming != previous:
        raise HTTPException(status_code=409, detail="ambiguous runtime capacity observation")


def reject_older_focus(previous: FocusSummary, incoming: FocusSummary) -> None:
    if incoming.updated_at < previous.updated_at:
        raise HTTPException(status_code=409, detail="older focus summary")
    if incoming.updated_at == previous.updated_at and incoming != previous:
        raise HTTPException(status_code=409, detail="ambiguous focus summary")


def reject_stale_full_snapshot(current: StatusSnapshot, incoming: StatusSnapshot) -> None:
    if incoming.observed_at < current.observed_at:
        raise HTTPException(status_code=409, detail="older status snapshot observation")
    incoming_by_agent = {item.agent: item for item in incoming.items}
    if len(incoming_by_agent) != len(incoming.items):
        raise HTTPException(status_code=409, detail="status snapshot has duplicate agents")
    for previous in current.items:
        replacement = incoming_by_agent.get(previous.agent)
        if replacement is not None:
            reject_older_item(previous, replacement, allow_same_observation_transition=True)
        elif current.source == "ingest-upsert" or incoming.observed_at <= current.observed_at:
            raise HTTPException(
                status_code=409, detail=f"stale full snapshot omits agent {previous.agent}"
            )
    if current.runtime_capacity is not None:
        if incoming.runtime_capacity is None:
            raise HTTPException(
                status_code=409, detail="full snapshot cannot clear runtime capacity"
            )
        reject_older_capacity(current.runtime_capacity, incoming.runtime_capacity)
    if current.focus_summary is not None:
        if incoming.focus_summary is None:
            raise HTTPException(status_code=409, detail="full snapshot cannot clear focus summary")
        reject_older_focus(current.focus_summary, incoming.focus_summary)


def principal_allowed(request: Request, expected_environment_key: str) -> bool:
    if os.environ.get("AGENT_WORLD_STATUS_REQUIRE_AUTH") != "true":
        return True
    expected = os.environ.get(expected_environment_key, "")
    actual = request.headers.get("x-ms-client-principal-id", "")
    try:
        return secrets.compare_digest(UUID(actual).bytes, UUID(expected).bytes)
    except ValueError:
        return False


def create_app(store: SnapshotStore | None = None) -> FastAPI:
    app = FastAPI(title="Agent World Management Status", version="0.4.0")
    snapshots = store or configured_store()
    ingest_lock = Lock()

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if request.url.path != "/healthz":
            principal_key = (
                "AGENT_WORLD_STATUS_INGEST_OBJECT_ID"
                if request.method == "PUT"
                and request.url.path in {"/api/status", "/api/status/upsert"}
                else "AGENT_WORLD_STATUS_OPERATOR_OBJECT_ID"
            )
            if not principal_allowed(request, principal_key):
                return JSONResponse(status_code=403, content={"detail": "forbidden"})
        response = await call_next(request)
        if request.method == "GET" and request.url.path in {
            "/",
            "/index.html",
            "/status.css",
            "/status.js",
        }:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status", response_model=StatusResponse)
    def get_status(response: Response) -> StatusResponse:
        response.headers["Cache-Control"] = "no-store"
        try:
            return status_response(snapshots.read())
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=503, detail="status snapshot has not been received"
            ) from error
        except (OSError, ValueError, ValidationError, SnapshotStoreError) as error:
            raise HTTPException(status_code=503, detail="status snapshot is invalid") from error

    @app.put("/api/status", status_code=status.HTTP_204_NO_CONTENT)
    def put_status(snapshot: StatusSnapshot) -> Response:
        if snapshot.source != "local-event-record":
            raise HTTPException(status_code=422, detail="only local-event-record is accepted")
        with ingest_lock:
            try:
                current_version = snapshots.read_versioned()
                current = current_version.snapshot
            except FileNotFoundError:
                current = None
                current_revision = None
            except (OSError, ValueError, ValidationError, SnapshotStoreError) as error:
                raise HTTPException(status_code=503, detail="status snapshot is invalid") from error
            else:
                current_revision = current_version.revision
            if current:
                same_content = current.model_dump(exclude={"received_at"}) == snapshot.model_dump(
                    exclude={"received_at"}
                )
                if same_content:
                    return Response(status_code=status.HTTP_204_NO_CONTENT)
                reject_stale_full_snapshot(current, snapshot)
                if (
                    snapshot.observed_at == current.observed_at
                    and snapshot.received_at <= current.received_at
                ):
                    raise HTTPException(
                        status_code=409, detail="full snapshot receipt is not newer"
                    )
            try:
                snapshots.write_if_revision(snapshot, current_revision)
            except SnapshotConflictError as error:
                raise HTTPException(
                    status_code=409, detail="status snapshot changed before write"
                ) from error
            except (OSError, SnapshotStoreError) as error:
                raise HTTPException(
                    status_code=503, detail="status snapshot write failed"
                ) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.put(
        "/api/status/upsert",
        response_model=StatusUpsertReceipt,
        response_model_exclude_none=True,
    )
    def upsert_status(update: StatusUpsertRequest) -> StatusUpsertReceipt:
        with ingest_lock:
            try:
                current_version = snapshots.read_versioned()
            except FileNotFoundError as error:
                raise HTTPException(
                    status_code=409, detail="status snapshot is required before partial update"
                ) from error
            except (OSError, ValueError, ValidationError, SnapshotStoreError) as error:
                raise HTTPException(status_code=503, detail="status snapshot is invalid") from error

            current = current_version.snapshot
            current_agents = [item.agent for item in current.items]
            if len(current_agents) != len(set(current_agents)):
                raise HTTPException(status_code=409, detail="status snapshot has duplicate agents")

            current_by_agent = {item.agent: item for item in current.items}
            for item in update.items:
                previous = current_by_agent.get(item.agent)
                if previous is not None:
                    reject_older_item(previous, item)
            capacity_supplied = "runtime_capacity" in update.model_fields_set
            if (
                capacity_supplied
                and update.runtime_capacity is not None
                and current.runtime_capacity is not None
            ):
                reject_older_capacity(current.runtime_capacity, update.runtime_capacity)
            focus_supplied = "focus_summary" in update.model_fields_set
            if (
                focus_supplied
                and update.focus_summary is not None
                and current.focus_summary is not None
            ):
                reject_older_focus(current.focus_summary, update.focus_summary)

            replacements = {item.agent: item for item in update.items}
            merged_items = [replacements.pop(item.agent, item) for item in current.items]
            merged_items.extend(replacements.values())
            merged_capacity = (
                update.runtime_capacity if capacity_supplied else current.runtime_capacity
            )
            merged_focus = update.focus_summary if focus_supplied else current.focus_summary
            observation_times = [current.observed_at, *(item.observed_at for item in update.items)]
            if capacity_supplied and update.runtime_capacity is not None:
                observation_times.append(update.runtime_capacity.observed_at)
            if focus_supplied and update.focus_summary is not None:
                observation_times.append(update.focus_summary.updated_at)
            now = datetime.now(UTC)
            try:
                merged = StatusSnapshot(
                    schema_version=1,
                    source="ingest-upsert",
                    observed_at=max(observation_times),
                    received_at=now,
                    items=merged_items,
                    runtime_capacity=merged_capacity,
                    focus_summary=merged_focus,
                )
            except ValidationError as error:
                raise HTTPException(status_code=422, detail="merged snapshot is invalid") from error

            same_content = current.model_dump(exclude={"received_at"}) == merged.model_dump(
                exclude={"received_at"}
            )
            if same_content:
                stored = current
                revision = current_version.revision
            else:
                try:
                    revision = snapshots.write_if_revision(merged, current_version.revision)
                except SnapshotConflictError as error:
                    raise HTTPException(
                        status_code=409, detail="status snapshot changed before write"
                    ) from error
                except (OSError, SnapshotStoreError) as error:
                    raise HTTPException(
                        status_code=503, detail="status snapshot write failed"
                    ) from error
                stored = merged

        stored_by_agent = {item.agent: item for item in stored.items}
        return StatusUpsertReceipt(
            changed=not same_content,
            revision=revision,
            items=[stored_by_agent[item.agent] for item in update.items],
            runtime_capacity=stored.runtime_capacity if capacity_supplied else None,
            focus_summary=stored.focus_summary if focus_supplied else None,
        )

    static_root = Path(__file__).parents[2] / "docs" / "status"
    app.mount("/", StaticFiles(directory=static_root, html=True), name="status-ui")
    return app


app = create_app()
