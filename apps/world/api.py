import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse

from .models import (
    AcceptPlanAction,
    Action,
    ActionResult,
    DispatchShipmentAction,
    EventHistory,
    LogisticsActionResult,
    LogisticsWorldState,
    ResetLogisticsScenario,
    WorldState,
)
from .simulator import LogisticsSimulator, WorldSimulator


def create_app(static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="AI Agent Sandbox World", version="0.1.0")
    simulator = WorldSimulator()
    logistics = LogisticsSimulator()
    configured_static_dir = static_dir or (
        Path(value) if (value := os.getenv("WORLD_STATIC_DIR")) else None
    )

    @app.get("/healthz", include_in_schema=True)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/world", response_model=WorldState)
    def observe(response: Response) -> WorldState:
        response.headers["Cache-Control"] = "no-store"
        return simulator.observe()

    @app.post("/api/actions", response_model=ActionResult)
    def act(action: Action) -> ActionResult:
        return simulator.apply(action)

    @app.get("/api/events", response_model=EventHistory)
    def events(response: Response) -> EventHistory:
        response.headers["Cache-Control"] = "no-store"
        return simulator.observe_events()

    @app.get("/api/logistics/world", response_model=LogisticsWorldState)
    def observe_logistics(response: Response) -> LogisticsWorldState:
        response.headers["Cache-Control"] = "no-store"
        return logistics.observe()

    @app.post("/api/logistics/scenario/reset", response_model=LogisticsWorldState)
    def reset_logistics(
        request: ResetLogisticsScenario,
        principal_id: Annotated[str, Header(alias="X-Local-Principal")],
    ) -> LogisticsWorldState:
        state = logistics.reset(principal_id, request.truck_count)
        if state is None:
            raise HTTPException(status_code=403, detail="authz_denied")
        return state

    @app.post("/api/logistics/plans/accept", response_model=LogisticsActionResult)
    def accept_logistics_plan(
        action: AcceptPlanAction,
        principal_id: Annotated[str, Header(alias="X-Local-Principal")],
    ) -> LogisticsActionResult:
        return logistics.accept_plan(principal_id, action)

    @app.post("/api/logistics/actions", response_model=LogisticsActionResult)
    def dispatch_logistics(
        action: DispatchShipmentAction,
        principal_id: Annotated[str, Header(alias="X-Local-Principal")],
    ) -> LogisticsActionResult:
        return logistics.dispatch(principal_id, action)

    if configured_static_dir is not None:
        static_root = configured_static_dir.resolve(strict=True)
        index_path = static_root / "index.html"
        if not index_path.is_file():
            raise RuntimeError(f"Static index is missing: {index_path}")

        @app.get("/{requested_path:path}", include_in_schema=False)
        def static(requested_path: str):
            if requested_path == "api" or requested_path.startswith("api/"):
                raise HTTPException(status_code=404)

            candidate = (static_root / requested_path).resolve()
            if not candidate.is_relative_to(static_root):
                raise HTTPException(status_code=404)
            if candidate.is_file():
                return FileResponse(candidate)
            if Path(requested_path).suffix:
                raise HTTPException(status_code=404)
            return FileResponse(index_path)

    return app


app = create_app()
