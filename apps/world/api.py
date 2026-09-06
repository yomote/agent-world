import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse

from .models import Action, ActionResult, WorldState
from .simulator import WorldSimulator


def create_app(static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="AI Agent Sandbox World", version="0.1.0")
    simulator = WorldSimulator()
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
