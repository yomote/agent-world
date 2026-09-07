"""本文を解釈せず、許可した構造化eventだけを観測する。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024


def thread_id(value: str) -> str:
    return str(UUID(value))


def observe(path: Path, owner: str) -> dict:
    owner = thread_id(owner)
    latest = None
    matched = False
    consumed = 0
    with path.open("rb") as source:
        for index in range(100_000):
            line = source.readline(MAX_LINE_BYTES + 1)
            consumed += len(line)
            if consumed > MAX_EVENT_BYTES or len(line) > MAX_LINE_BYTES:
                raise ValueError("event_input_limit")
            if not line or not line.endswith(b"\n"):
                break
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
                continue
            payload = record["payload"]
            if index < 2 and record.get("type") == "session_meta":
                ids = [payload[key] for key in ("id", "session_id") if key in payload]
                if not ids or any(value != owner for value in ids) or matched:
                    raise ValueError("session_identity_mismatch")
                matched = True
            if not matched or record.get("type") != "event_msg":
                continue
            kind = payload.get("type")
            if kind not in ("task_started", "task_complete"):
                continue
            try:
                timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    continue
                timestamp = timestamp.astimezone(UTC).isoformat()
            except (KeyError, AttributeError, TypeError, ValueError):
                continue
            if latest is None or timestamp >= latest["observed_at"]:
                latest = {"observed_at": timestamp, "event": kind}
        else:
            raise ValueError("event_record_limit")
    if not matched:
        raise ValueError("session_identity_missing")
    return {"source": "local-event-record", "owner": owner, "latest": latest}
