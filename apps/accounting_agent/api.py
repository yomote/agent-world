import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from world.accounting_simulator import AccountingDomainError, AccountingSimulator

from .controller import AccountingAgentController
from .models import AdvanceRequest, AnswerRequest, RunView, StartRunRequest
from .provider import CodexExecProvider, FixedWorkflowProvider, GlobalCallBudget
from .store import RunStore


def create_app(database_path: Path | None = None, fixture_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="Accounting Cash Application Agent", version="0.1.0")
    db_path = database_path or Path(
        os.getenv("ACCOUNTING_RUN_DB", "artifacts/accounting/runs.sqlite3")
    )
    store = RunStore(db_path)
    simulator = AccountingSimulator(fixture_path)
    budget = GlobalCallBudget(
        limit=int(os.getenv("ACCOUNTING_MODEL_CALL_LIMIT", "24")),
        used=int(os.getenv("ACCOUNTING_MODEL_CALLS_ALREADY_USED", "0")),
    )
    controllers = {
        "agent": AccountingAgentController(simulator, store, CodexExecProvider(budget)),
        "baseline": AccountingAgentController(simulator, store, FixedWorkflowProvider()),
    }

    @app.get("/healthz")
    def health() -> dict:
        return {
            "status": "ok",
            "model_budget_limit": budget.limit,
            "model_budget_used": budget.used,
            "model_budget_remaining": budget.remaining,
        }

    @app.post("/api/accounting/runs", response_model=RunView)
    def start(request: StartRunRequest) -> dict:
        controller = controllers[request.mode]
        run_id = controller.start(request.mode)
        return controller.view(run_id)

    @app.post("/api/accounting/runs/{run_id}/advance", response_model=RunView)
    def advance(run_id: str, request: AdvanceRequest) -> dict:
        return _invoke(
            run_id,
            lambda controller: controller.advance(
                run_id, request.action_id, request.expected_step_version
            ),
        )

    @app.post("/api/accounting/runs/{run_id}/answer", response_model=RunView)
    def answer(run_id: str, request: AnswerRequest) -> dict:
        return _invoke(
            run_id,
            lambda controller: controller.answer(
                run_id,
                request.action_id,
                request.expected_step_version,
                request.question_id,
                request.answer,
            ),
        )

    @app.get("/api/accounting/runs/{run_id}", response_model=RunView)
    def run(run_id: str) -> dict:
        return _invoke(run_id, lambda controller: controller.view(run_id))

    @app.get("/api/accounting/runs")
    def runs() -> list[dict]:
        return store.list_runs()

    def _invoke(run_id: str, function):
        try:
            run = store.get_run(run_id)
            if run is None:
                raise KeyError(run_id)
            mode = run["mode"]
            return function(controllers[mode])
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown_run") from error
        except AccountingDomainError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return app


app = create_app()
