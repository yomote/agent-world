import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse

from .incident_models import (
    ApplyRequest,
    ApprovalRequest,
    ExecutionRequest,
    IncidentRunRequest,
    IncidentSnapshot,
    IncidentToolRequest,
    IncidentToolResult,
    ProposalResult,
    RecoveryProposal,
)
from .local_auth import LabCapabilities, require_capability
from .models import Action, ActionResult, EventHistory, WorldState
from .simulator import IncidentSimulator, WorldSimulator


def create_app(
    static_dir: Path | None = None, capabilities: LabCapabilities | None = None
) -> FastAPI:
    app = FastAPI(title="AI Agent Sandbox World", version="0.1.0")
    simulator = WorldSimulator()
    incidents = IncidentSimulator()
    caps = capabilities or LabCapabilities.from_environment()
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

    @app.post("/api/incidents/runs", response_model=IncidentSnapshot)
    def start_incident(
        request: IncidentRunRequest, _: str = Depends(require_capability(caps.operator))
    ) -> IncidentSnapshot:
        return incidents.reset(request)

    @app.get("/api/incidents/runs/{run_id}", response_model=IncidentSnapshot)
    def observe_incident(
        run_id: str,
        response: Response,
        _: str = Depends(require_capability(caps.observer)),
    ) -> IncidentSnapshot:
        response.headers["Cache-Control"] = "no-store"
        try:
            return incidents.observe(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="incident_not_found") from error

    @app.post("/api/incidents/runs/{run_id}/tools", response_model=IncidentToolResult)
    def incident_tool(
        run_id: str,
        request: IncidentToolRequest,
        x_lab_capability: str | None = Header(default=None),
    ) -> IncidentToolResult:
        expected = caps.executor if request.tool == "advance_and_verify" else caps.observer
        if x_lab_capability != expected:
            raise HTTPException(status_code=403, detail="capability_denied")
        try:
            return incidents.tool(run_id, request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="incident_not_found") from error

    @app.post("/api/incidents/runs/{run_id}/proposals/validate", response_model=ProposalResult)
    def validate_proposal(
        run_id: str,
        proposal: RecoveryProposal,
        _: str = Depends(require_capability(caps.proposer)),
    ) -> ProposalResult:
        try:
            return incidents.validate(run_id, proposal)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="incident_not_found") from error

    @app.post("/api/incidents/runs/{run_id}/proposals", response_model=ProposalResult)
    def request_execution(
        run_id: str,
        request: ExecutionRequest,
        _: str = Depends(require_capability(caps.proposer)),
    ) -> ProposalResult:
        try:
            return incidents.request_execution(run_id, request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="incident_not_found") from error

    @app.post("/api/incidents/runs/{run_id}/approval", response_model=ProposalResult)
    def approve_execution(
        run_id: str,
        request: ApprovalRequest,
        _: str = Depends(require_capability(caps.operator)),
    ) -> ProposalResult:
        try:
            return incidents.approve(run_id, request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="incident_not_found") from error

    @app.post("/api/incidents/runs/{run_id}/apply", response_model=ProposalResult)
    def apply_execution(
        run_id: str,
        request: ApplyRequest,
        _: str = Depends(require_capability(caps.executor)),
    ) -> ProposalResult:
        try:
            return incidents.apply_approved(run_id, request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="incident_not_found") from error

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
