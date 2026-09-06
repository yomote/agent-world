import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .models import StatusResponse, StatusSnapshot

STALE_AFTER_SECONDS = 120


def snapshot_path() -> Path:
    configured = os.environ.get("AGENT_WORLD_STATUS_SNAPSHOT")
    return Path(configured) if configured else Path("artifacts/status/current.json")


def load_snapshot(now: datetime | None = None) -> StatusResponse:
    data = json.loads(snapshot_path().read_text(encoding="utf-8"))
    snapshot = StatusSnapshot.model_validate(data)
    current = now or datetime.now(UTC)
    received = snapshot.received_at.astimezone(UTC)
    age_seconds = max(0, int((current - received).total_seconds()))
    return StatusResponse(
        **snapshot.model_dump(), stale=age_seconds > STALE_AFTER_SECONDS, age_seconds=age_seconds
    )
