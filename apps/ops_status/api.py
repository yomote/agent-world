import hashlib
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
    FrontDeskClaim,
    RegistryHandover,
    RequestRecord,
    RequestRegistryReceipt,
    RequestRegistrySnapshot,
    RequestRegistryUpdate,
    RuntimeCapacitySnapshot,
    StatusResponse,
    StatusRuntimeBinding,
    StatusSnapshot,
    StatusUpsertReceipt,
    StatusUpsertRequest,
    WorkItem,
    validate_pm_task_projection_transition,
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


def reject_clocked_snapshot(previous, incoming, *, clock: str, label: str) -> None:
    previous_time = getattr(previous, clock)
    incoming_time = getattr(incoming, clock)
    if incoming_time < previous_time:
        raise HTTPException(status_code=409, detail=f"older {label}")
    if incoming_time == previous_time and incoming != previous:
        raise HTTPException(status_code=409, detail=f"ambiguous {label}")


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
    for field, clock, label in (
        ("session_tree", "observed_at", "session tree"),
        ("known_history", "recorded_at", "known history"),
        ("pm_task_projection", "observed_at", "PM task projection"),
    ):
        previous = getattr(current, field)
        replacement = getattr(incoming, field)
        if previous is not None:
            if replacement is None:
                raise HTTPException(status_code=409, detail=f"full snapshot cannot clear {label}")
            if field == "pm_task_projection":
                try:
                    validate_pm_task_projection_transition(previous, replacement)
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
            reject_clocked_snapshot(previous, replacement, clock=clock, label=label)
    if (
        current.request_registry is not None
        and incoming.request_registry != current.request_registry
    ):
        raise HTTPException(status_code=409, detail="full snapshot cannot replace request registry")


def reject_older_request(previous: RequestRecord, incoming: RequestRecord) -> None:
    """依頼の正本観測、公開報告、runtime観測を別々の時計で単調に保つ。"""
    comparisons = (
        (
            previous.issue_observed_at,
            incoming.issue_observed_at,
            (
                previous.issue_url,
                previous.issue_state,
                previous.issue_observation,
                previous.public_title,
                previous.public_purpose,
                previous.acceptance_summary,
            ),
            (
                incoming.issue_url,
                incoming.issue_state,
                incoming.issue_observation,
                incoming.public_title,
                incoming.public_purpose,
                incoming.acceptance_summary,
            ),
            "Issue observation",
        ),
        (
            previous.report_updated_at,
            incoming.report_updated_at,
            (
                previous.lifecycle,
                previous.owner_agent,
                previous.member_agents,
                previous.progress_summary,
                previous.blocker,
                previous.next_action,
                previous.resume_trigger,
                previous.dod_source_version,
                previous.required_requirement_ids,
                previous.requirements_contract_digest,
                previous.expected_artifact_head,
                previous.closure_audit,
                previous.po_review_required,
                previous.po_acceptance_receipt,
                previous.evidence,
            ),
            (
                incoming.lifecycle,
                incoming.owner_agent,
                incoming.member_agents,
                incoming.progress_summary,
                incoming.blocker,
                incoming.next_action,
                incoming.resume_trigger,
                incoming.dod_source_version,
                incoming.required_requirement_ids,
                incoming.requirements_contract_digest,
                incoming.expected_artifact_head,
                incoming.closure_audit,
                incoming.po_review_required,
                incoming.po_acceptance_receipt,
                incoming.evidence,
            ),
            "request report",
        ),
        (
            previous.runtime_observed_at,
            incoming.runtime_observed_at,
            previous.runtime_connection,
            incoming.runtime_connection,
            "runtime connection",
        ),
    )
    for previous_time, incoming_time, previous_value, incoming_value, label in comparisons:
        if previous_time is not None and (incoming_time is None or incoming_time < previous_time):
            raise HTTPException(status_code=409, detail=f"older or ambiguous {label}")
        if previous_value != incoming_value and (
            incoming_time is None or incoming_time == previous_time
        ):
            raise HTTPException(status_code=409, detail=f"older or ambiguous {label}")


def apply_registry_update(
    current: RequestRegistrySnapshot | None, update: RequestRegistryUpdate
) -> tuple[RequestRegistrySnapshot, bool]:
    if current is None:
        if update.action != "initialize":
            raise HTTPException(status_code=409, detail="request registry is not initialized")
        if any(request.lifecycle == "completed" for request in update.requests):
            raise HTTPException(
                status_code=409,
                detail="completed request needs a previously saved requirements contract",
            )
        registry = RequestRegistrySnapshot(
            generation=1,
            active_front_desk=FrontDeskClaim(
                alias=update.actor_front_desk,
                claimed_at=update.observed_at,
                claim_generation=1,
                runtime_session_id=update.actor_runtime_session_id,
                runtime_observed_at=(
                    update.observed_at if update.actor_runtime_session_id is not None else None
                ),
            ),
            updated_at=update.observed_at,
            source=update.source,
            requests=update.requests,
        )
        return registry, True
    if update.action == "initialize":
        raise HTTPException(status_code=409, detail="request registry is already initialized")
    if update.expected_generation != current.generation:
        raise HTTPException(status_code=409, detail="request registry generation changed")

    if update.action == "claim-handover":
        handover = current.handover
        if (
            handover is None
            or handover.state != "ready"
            or update.actor_front_desk != handover.to_front_desk
            or update.successor_front_desk != handover.to_front_desk
            or update.bundle_digest != handover.bundle_digest
        ):
            raise HTTPException(
                status_code=409, detail="handover claim does not match prepared bundle"
            )
    elif update.actor_front_desk != current.active_front_desk.alias:
        raise HTTPException(
            status_code=409, detail="request registry has a different active Front Desk"
        )

    requests = {request.request_id: request for request in current.requests}
    occupied_scopes = {request.scope_id: request.request_id for request in current.requests}
    changed = False
    for incoming in update.requests:
        previous = requests.get(incoming.request_id)
        if previous is not None:
            if incoming.scope_id != previous.scope_id:
                raise HTTPException(status_code=409, detail="request scope identity cannot change")
            reject_older_request(previous, incoming)
            if incoming.lifecycle == "completed" and previous.lifecycle != "completed":
                if (
                    not previous.required_requirement_ids
                    or previous.requirements_contract_digest is None
                    or previous.expected_artifact_head is None
                    or previous.dod_source_version is None
                    or incoming.required_requirement_ids != previous.required_requirement_ids
                    or incoming.requirements_contract_digest
                    != previous.requirements_contract_digest
                    or incoming.expected_artifact_head != previous.expected_artifact_head
                    or incoming.dod_source_version != previous.dod_source_version
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="completion must use the previously saved requirements contract",
                    )
        else:
            if incoming.lifecycle == "completed":
                raise HTTPException(
                    status_code=409,
                    detail="completed request needs a previously saved requirements contract",
                )
            if incoming.scope_id in occupied_scopes:
                raise HTTPException(
                    status_code=409, detail="request scope identity is already used"
                )
        if incoming != previous:
            requests[incoming.request_id] = incoming
            changed = True

    active = current.active_front_desk
    handover = current.handover
    if update.action == "prepare-handover":
        if update.successor_front_desk == active.alias:
            raise HTTPException(status_code=409, detail="handover needs a different successor")
        handover = RegistryHandover(
            state="ready",
            from_front_desk=active.alias,
            to_front_desk=update.successor_front_desk,
            bundle_digest=update.bundle_digest,
            prepared_at=update.observed_at,
            resume_policy="explicit-dispatch-required",
        )
        changed = True
    elif update.action == "claim-handover":
        if update.observed_at <= handover.prepared_at:
            raise HTTPException(status_code=409, detail="handover claim clock is not newer")
        active = FrontDeskClaim(
            alias=update.actor_front_desk,
            claimed_at=update.observed_at,
            claim_generation=current.generation + 1,
            runtime_session_id=update.actor_runtime_session_id,
            runtime_observed_at=(
                update.observed_at if update.actor_runtime_session_id is not None else None
            ),
        )
        handover = handover.model_copy(
            update={"state": "accepted", "accepted_at": update.observed_at}
        )
        requests = {
            request_id: request.model_copy(
                update={
                    "lifecycle": (
                        request.lifecycle
                        if request.lifecycle == "completed"
                        else "handover-waiting"
                    ),
                    "report_updated_at": (
                        request.report_updated_at
                        if request.lifecycle == "completed"
                        else update.observed_at
                    ),
                    "runtime_connection": (
                        request.runtime_connection
                        if request.lifecycle == "completed"
                        else "record-only"
                    ),
                    "runtime_observed_at": (
                        request.runtime_observed_at
                        if request.lifecycle == "completed"
                        else update.observed_at
                    ),
                    "resume_trigger": (
                        request.resume_trigger
                        if request.lifecycle == "completed" or request.resume_trigger is not None
                        else "成功claim receiptを確認後に明示dispatch"
                    ),
                }
            )
            for request_id, request in requests.items()
        }
        changed = True

    if not changed:
        return current, False
    if update.observed_at <= current.updated_at:
        raise HTTPException(status_code=409, detail="request registry clock is not newer")
    return RequestRegistrySnapshot(
        generation=current.generation + 1,
        active_front_desk=active,
        updated_at=update.observed_at,
        source=current.source,
        requests=list(requests.values()),
        handover=handover,
    ), True


def require_active_runtime_binding(
    registry: RequestRegistrySnapshot | None, binding: StatusRuntimeBinding | None
) -> None:
    """handover後のstatusをclaim済みroot以外から受け取らない。"""
    if registry is None or registry.active_front_desk.runtime_session_id is None:
        if binding is not None:
            raise HTTPException(status_code=409, detail="active Front Desk runtime is not bound")
        return
    active = registry.active_front_desk
    expected_digest = f"sha256:{hashlib.sha256(active.runtime_session_id.encode()).hexdigest()}"
    legacy_claim = (
        active.claim_generation is None
        and registry.handover is not None
        and registry.handover.state == "accepted"
        and registry.handover.to_front_desk == active.alias
        and registry.handover.accepted_at == active.claimed_at
    )
    generation_matches = binding is not None and (
        binding.registry_generation == active.claim_generation
        or (legacy_claim and binding.registry_generation <= registry.generation)
    )
    if (
        binding is None
        or not generation_matches
        or binding.front_desk_alias != active.alias
        or binding.runtime_session_digest != expected_digest
    ):
        raise HTTPException(
            status_code=409, detail="status update does not match the active Front Desk runtime"
        )


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
    app = FastAPI(title="Agent World Management Status", version="0.6.0")
    snapshots = store or configured_store()
    ingest_lock = Lock()

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if request.url.path != "/healthz":
            principal_key = (
                "AGENT_WORLD_STATUS_INGEST_OBJECT_ID"
                if request.method == "PUT"
                and request.url.path
                in {
                    "/api/status",
                    "/api/status/upsert",
                    "/api/status/requests/upsert",
                }
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

    @app.get("/api/status", response_model=StatusResponse, response_model_exclude_none=True)
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
            if current is not None:
                require_active_runtime_binding(current.request_registry, snapshot.runtime_binding)
            if snapshot.pm_task_projection is not None and (
                current is None or current.pm_task_projection is None
            ):
                raise HTTPException(
                    status_code=409,
                    detail="full snapshot cannot initialize PM task projection",
                )
            if snapshot.request_registry is not None and (
                current is None or current.request_registry is None
            ):
                raise HTTPException(
                    status_code=409,
                    detail="full snapshot cannot initialize request registry",
                )
            if current:
                same_content = current.model_dump(exclude={"received_at"}) == snapshot.model_dump(
                    exclude={"received_at"}
                )
                if same_content:
                    return Response(status_code=status.HTTP_204_NO_CONTENT)
                reject_stale_full_snapshot(current, snapshot)
                if snapshot.pm_task_projection != current.pm_task_projection:
                    raise HTTPException(
                        status_code=409,
                        detail="runtime ingest cannot replace PM task projection",
                    )
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
            if update.source == "local-event-record":
                require_active_runtime_binding(current.request_registry, update.runtime_binding)
            binding_activates_current = bool(
                update.source == "local-event-record"
                and update.runtime_binding is not None
                and current.runtime_binding is None
                and current.request_registry is not None
                and current.request_registry.active_front_desk.runtime_session_id is not None
            )
            current_items = [] if binding_activates_current else current.items
            current_agents = [item.agent for item in current_items]
            if len(current_agents) != len(set(current_agents)):
                raise HTTPException(status_code=409, detail="status snapshot has duplicate agents")

            current_by_agent = {item.agent: item for item in current_items}
            for item in update.items:
                previous = current_by_agent.get(item.agent)
                if previous is not None:
                    reject_older_item(previous, item)
            capacity_supplied = "runtime_capacity" in update.model_fields_set
            if (
                not binding_activates_current
                and capacity_supplied
                and update.runtime_capacity is not None
                and current.runtime_capacity is not None
            ):
                reject_older_capacity(current.runtime_capacity, update.runtime_capacity)
            focus_supplied = "focus_summary" in update.model_fields_set
            if (
                not binding_activates_current
                and focus_supplied
                and update.focus_summary is not None
                and current.focus_summary is not None
            ):
                reject_older_focus(current.focus_summary, update.focus_summary)
            tree_supplied = "session_tree" in update.model_fields_set
            if (
                not binding_activates_current
                and tree_supplied
                and update.session_tree is not None
                and current.session_tree is not None
            ):
                reject_clocked_snapshot(
                    current.session_tree,
                    update.session_tree,
                    clock="observed_at",
                    label="session tree",
                )
            history_supplied = "known_history" in update.model_fields_set
            if (
                history_supplied
                and update.known_history is not None
                and current.known_history is not None
            ):
                reject_clocked_snapshot(
                    current.known_history,
                    update.known_history,
                    clock="recorded_at",
                    label="known history",
                )
            projection_supplied = "pm_task_projection" in update.model_fields_set
            if (
                projection_supplied
                and update.pm_task_projection is not None
                and current.pm_task_projection is not None
            ):
                try:
                    validate_pm_task_projection_transition(
                        current.pm_task_projection, update.pm_task_projection
                    )
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                reject_clocked_snapshot(
                    current.pm_task_projection,
                    update.pm_task_projection,
                    clock="observed_at",
                    label="PM task projection",
                )

            replacements = {item.agent: item for item in update.items}
            merged_items = [replacements.pop(item.agent, item) for item in current_items]
            merged_items.extend(replacements.values())
            merged_capacity = (
                update.runtime_capacity
                if capacity_supplied
                else (None if binding_activates_current else current.runtime_capacity)
            )
            merged_focus = (
                update.focus_summary
                if focus_supplied
                else (None if binding_activates_current else current.focus_summary)
            )
            merged_tree = (
                update.session_tree
                if tree_supplied
                else (None if binding_activates_current else current.session_tree)
            )
            merged_history = update.known_history if history_supplied else current.known_history
            merged_projection = (
                update.pm_task_projection if projection_supplied else current.pm_task_projection
            )
            merged_binding = update.runtime_binding or current.runtime_binding
            observation_times = [current.observed_at, *(item.observed_at for item in update.items)]
            if capacity_supplied and update.runtime_capacity is not None:
                observation_times.append(update.runtime_capacity.observed_at)
            if focus_supplied and update.focus_summary is not None:
                observation_times.append(update.focus_summary.updated_at)
            if tree_supplied and update.session_tree is not None:
                observation_times.append(update.session_tree.observed_at)
            if history_supplied and update.known_history is not None:
                observation_times.append(update.known_history.recorded_at)
            if projection_supplied and update.pm_task_projection is not None:
                observation_times.append(update.pm_task_projection.observed_at)
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
                    session_tree=merged_tree,
                    known_history=merged_history,
                    request_registry=current.request_registry,
                    pm_task_projection=merged_projection,
                    runtime_binding=merged_binding,
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
            session_tree=stored.session_tree if tree_supplied else None,
            known_history=stored.known_history if history_supplied else None,
            pm_task_projection=stored.pm_task_projection if projection_supplied else None,
            runtime_binding=stored.runtime_binding if update.runtime_binding is not None else None,
        )

    @app.put(
        "/api/status/requests/upsert",
        response_model=RequestRegistryReceipt,
        response_model_exclude_none=True,
    )
    def upsert_request_registry(update: RequestRegistryUpdate) -> RequestRegistryReceipt:
        with ingest_lock:
            try:
                current_version = snapshots.read_versioned()
            except FileNotFoundError as error:
                raise HTTPException(
                    status_code=409, detail="status snapshot is required"
                ) from error
            except (OSError, ValueError, ValidationError, SnapshotStoreError) as error:
                raise HTTPException(status_code=503, detail="status snapshot is invalid") from error
            current = current_version.snapshot
            registry, changed = apply_registry_update(current.request_registry, update)
            if changed:
                try:
                    runtime_reset = update.action == "claim-handover"
                    merged = current.model_copy(
                        update={
                            "source": "ingest-upsert",
                            "observed_at": max(current.observed_at, update.observed_at),
                            "received_at": datetime.now(UTC),
                            "request_registry": registry,
                            # claimと同じBlob CASで旧rootのruntime表示を無効化する。
                            "items": [] if runtime_reset else current.items,
                            "runtime_capacity": (
                                None if runtime_reset else current.runtime_capacity
                            ),
                            "focus_summary": None if runtime_reset else current.focus_summary,
                            "session_tree": None if runtime_reset else current.session_tree,
                            # 完了履歴はcurrent runtimeとは別の記録として保持する。
                            "known_history": current.known_history,
                            "runtime_binding": None if runtime_reset else current.runtime_binding,
                        }
                    )
                    revision = snapshots.write_if_revision(merged, current_version.revision)
                except SnapshotConflictError as error:
                    raise HTTPException(
                        status_code=409, detail="status snapshot changed before write"
                    ) from error
                except (OSError, SnapshotStoreError) as error:
                    raise HTTPException(
                        status_code=503, detail="status snapshot write failed"
                    ) from error
            else:
                revision = current_version.revision
            by_id = {request.request_id: request for request in registry.requests}
            return RequestRegistryReceipt(
                changed=changed,
                revision=revision,
                generation=registry.generation,
                active_front_desk=(
                    registry.active_front_desk
                    if update.action in {"initialize", "claim-handover"}
                    else None
                ),
                requests=[by_id[request.request_id] for request in update.requests],
                handover=(
                    registry.handover
                    if update.action in {"prepare-handover", "claim-handover"}
                    else None
                ),
            )

    static_root = Path(__file__).parents[2] / "docs" / "status"
    app.mount("/", StaticFiles(directory=static_root, html=True), name="status-ui")
    return app


app = create_app()
