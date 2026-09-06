import os
import secrets
from pathlib import Path
from threading import Lock
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .models import StatusResponse, StatusSnapshot
from .store import SnapshotStore, SnapshotStoreError, configured_store, status_response


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
    app = FastAPI(title="Agent World Management Status", version="0.2.0")
    snapshots = store or configured_store()
    ingest_lock = Lock()

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if request.url.path != "/healthz":
            principal_key = (
                "AGENT_WORLD_STATUS_INGEST_OBJECT_ID"
                if request.method == "PUT" and request.url.path == "/api/status"
                else "AGENT_WORLD_STATUS_OPERATOR_OBJECT_ID"
            )
            if not principal_allowed(request, principal_key):
                return JSONResponse(status_code=403, content={"detail": "forbidden"})
        return await call_next(request)

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
                current = snapshots.read()
            except FileNotFoundError:
                current = None
            except (OSError, ValueError, ValidationError, SnapshotStoreError) as error:
                raise HTTPException(status_code=503, detail="status snapshot is invalid") from error
            if current and snapshot.observed_at <= current.observed_at:
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            try:
                snapshots.write(snapshot)
            except (OSError, SnapshotStoreError) as error:
                raise HTTPException(
                    status_code=503, detail="status snapshot write failed"
                ) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    static_root = Path(__file__).parents[2] / "docs" / "status"
    app.mount("/", StaticFiles(directory=static_root, html=True), name="status-ui")
    return app


app = create_app()
