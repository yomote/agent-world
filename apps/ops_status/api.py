from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .models import StatusResponse
from .store import load_snapshot


def create_app() -> FastAPI:
    app = FastAPI(title="Agent World Management Status", version="0.1.0")

    @app.get("/api/status", response_model=StatusResponse)
    def status(response: Response) -> StatusResponse:
        response.headers["Cache-Control"] = "no-store"
        try:
            return load_snapshot()
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=503, detail="status snapshot has not been received"
            ) from error
        except (OSError, ValueError, ValidationError) as error:
            raise HTTPException(status_code=503, detail="status snapshot is invalid") from error

    static_root = Path(__file__).parents[2] / "docs" / "status"
    app.mount("/", StaticFiles(directory=static_root, html=True), name="status-ui")
    return app


app = create_app()
