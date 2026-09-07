from fastapi import FastAPI, Response

from .models import Action, ActionResult, EventHistory, WorldState
from .simulator import WorldSimulator


def create_app() -> FastAPI:
    app = FastAPI(title="AI Agent Sandbox World", version="0.1.0")
    simulator = WorldSimulator()

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

    return app


app = create_app()
