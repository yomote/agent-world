from fastapi import FastAPI, HTTPException

from .controller import HttpWorldGateway, IncidentController
from .models import AgentRun, HumanAnswer, HumanApproval, StartAgentRequest
from .provider import CodexExecProvider, GlobalCallBudget, RunbookProvider


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic Incident Recovery Controller", version="0.1.0")
    budget = GlobalCallBudget(limit=16)
    controller = IncidentController(
        HttpWorldGateway(),
        lambda mode: CodexExecProvider(budget) if mode == "actual" else RunbookProvider(),
    )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/agent/runs", response_model=AgentRun)
    def start(request: StartAgentRequest) -> AgentRun:
        return controller.start(request.scenario_number, request.mode)

    @app.get("/agent/runs/{run_id}", response_model=AgentRun)
    def observe(run_id: str) -> AgentRun:
        try:
            return controller.public(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="run_not_found") from error

    @app.post("/agent/runs/{run_id}/advance", response_model=AgentRun)
    def advance(run_id: str) -> AgentRun:
        try:
            return controller.advance(run_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="run_not_found") from error

    @app.post("/agent/runs/{run_id}/answer", response_model=AgentRun)
    def answer(run_id: str, answer: HumanAnswer) -> AgentRun:
        try:
            return controller.answer(run_id, answer.answer)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/agent/runs/{run_id}/approval", response_model=AgentRun)
    def approve(run_id: str, approval: HumanApproval) -> AgentRun:
        try:
            return controller.approve(run_id, approval.approve, approval.approver)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/agent/runs/{run_id}/reconcile", response_model=AgentRun)
    def reconcile(run_id: str) -> AgentRun:
        try:
            return controller.reconcile(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return app


app = create_app()
