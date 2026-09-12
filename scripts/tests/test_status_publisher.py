import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import AwareDatetime, BaseModel, ConfigDict

spec = importlib.util.spec_from_file_location(
    "publish_status_snapshot", Path(__file__).parents[1] / "publish_status_snapshot.py"
)
publish_status_snapshot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = publish_status_snapshot
spec.loader.exec_module(publish_status_snapshot)
publish_if_new = publish_status_snapshot.publish_if_new


class V1WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    role: str
    task: str
    status: Literal["running", "idle", "unknown", "review-wait", "blocked"]
    observed_at: AwareDatetime
    issue_url: str | None = None
    pr_url: str | None = None
    note: str | None = None


class V1Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    source: Literal["local-event-record"]
    observed_at: AwareDatetime
    received_at: AwareDatetime
    items: list[V1WorkItem]


def snapshot(observed_at: datetime, status: str = "running") -> dict:
    return {
        "schema_version": 1,
        "source": "local-event-record",
        "observed_at": observed_at.isoformat(),
        "received_at": observed_at.isoformat(),
        "items": [
            {
                "agent": "status owner",
                "role": "Implementation",
                "task": "Live status delivery",
                "status": status,
                "observed_at": observed_at.isoformat(),
            }
        ],
    }


def args(tmp_path, observed_at: datetime) -> argparse.Namespace:
    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(snapshot(observed_at)), encoding="utf-8")
    return argparse.Namespace(
        base_url="https://status.example.test",
        audience="api://status",
        ingest_client_id="ingest-client",
        snapshot=source,
        state=tmp_path / "state.json",
    )


def test_publisher_sends_new_snapshot_once(tmp_path):
    """同じeventをwatchの次tickで再送する回帰を防ぐ。"""
    settings = args(tmp_path, datetime.now(UTC))
    sends = []

    def send(url, token, payload):
        sends.append((url, token, payload))
        return 204

    assert publish_if_new(settings, lambda *_: "token", send) is True
    assert publish_if_new(settings, lambda *_: "token", send) is False
    assert len(sends) == 1
    V1Snapshot.model_validate_json(sends[0][2])
    assert json.loads(settings.state.read_text())["outcome"] == "confirmed"


def test_publisher_does_not_send_older_snapshot(tmp_path):
    """local file rollbackで古い状態をAzureへ送る回帰を防ぐ。"""
    current = datetime.now(UTC)
    settings = args(tmp_path, current)
    settings.state.write_text(
        json.dumps(
            {
                "last_attempted_observed_at": current.isoformat(),
                "outcome": "confirmed",
            }
        ),
        encoding="utf-8",
    )
    older = current.replace(year=current.year - 1)
    settings.snapshot.write_text(json.dumps(snapshot(older)), encoding="utf-8")

    assert publish_if_new(settings, lambda *_: "token", lambda *_: 204) is False


def test_publisher_sends_stale_transition_for_the_same_source_observation(tmp_path):
    """同じtask event時刻のrunning→unknown遷移を重複扱いして落とす回帰を防ぐ。"""
    observed_at = datetime.now(UTC)
    settings = args(tmp_path, observed_at)
    sends = []

    assert publish_if_new(settings, lambda *_: "token", lambda *call: sends.append(call) or 204)
    settings.snapshot.write_text(
        json.dumps(snapshot(observed_at, status="unknown")), encoding="utf-8"
    )

    assert publish_if_new(settings, lambda *_: "token", lambda *call: sends.append(call) or 204)
    assert len(sends) == 2


def test_publisher_rejects_older_snapshot_after_its_own_confirmed_write(tmp_path):
    """digest付き実運用stateで古い観測を不要PUTする回帰を防ぐ。"""
    observed_at = datetime.now(UTC)
    settings = args(tmp_path, observed_at)
    sends = []

    assert publish_if_new(settings, lambda *_: "token", lambda *call: sends.append(call) or 204)
    settings.snapshot.write_text(
        json.dumps(snapshot(observed_at - timedelta(days=1), status="unknown")), encoding="utf-8"
    )

    assert (
        publish_if_new(settings, lambda *_: "token", lambda *call: sends.append(call) or 204)
        is False
    )
    assert len(sends) == 1


@pytest.mark.parametrize("next_observation_delay", [0, 1])
def test_publisher_stops_after_unknown_write_result(tmp_path, next_observation_delay):
    """応答喪失後に同じPUTや後続snapshotを自動送信する回帰を防ぐ。"""
    settings = args(tmp_path, datetime.now(UTC))
    sends = 0

    def fail(*_):
        nonlocal sends
        sends += 1
        raise TimeoutError("response lost")

    with pytest.raises(TimeoutError):
        publish_if_new(settings, lambda *_: "token", fail)
    observed_at = datetime.fromisoformat(json.loads(settings.snapshot.read_text())["observed_at"])
    settings.snapshot.write_text(
        json.dumps(snapshot(observed_at + timedelta(seconds=next_observation_delay))),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="result is unknown"):
        publish_if_new(settings, lambda *_: "token", fail)
    assert sends == 1


def test_publisher_stops_after_interrupted_attempt(tmp_path):
    """送信後のprocess停止を挟み、未確定writeを新snapshotで上書きする回帰を防ぐ。"""
    observed_at = datetime.now(UTC)
    settings = args(tmp_path, observed_at)
    token_calls = []
    sends = []

    def token(*_):
        token_calls.append(True)
        return "token"

    def interrupt_after_send(*_):
        sends.append(True)
        raise KeyboardInterrupt("process stopped before confirmation was saved")

    with pytest.raises(KeyboardInterrupt):
        publish_if_new(settings, token, interrupt_after_send)
    persisted = settings.state.read_bytes()
    assert json.loads(persisted)["outcome"] == "attempting"
    settings.snapshot.write_text(
        json.dumps(snapshot(observed_at + timedelta(seconds=1))), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="result is unknown"):
        publish_if_new(settings, token, interrupt_after_send)

    assert len(token_calls) == len(sends) == 1
    assert settings.state.read_bytes() == persisted


def test_publisher_rejects_manual_snapshot(tmp_path):
    """PM手動snapshotをevent sourceとして外部送信する回帰を防ぐ。"""
    settings = args(tmp_path, datetime.now(UTC))
    data = json.loads(settings.snapshot.read_text())
    data["source"] = "pm-confirmed"
    settings.snapshot.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="local-event-record"):
        publish_if_new(settings, lambda *_: "token", lambda *_: 204)
