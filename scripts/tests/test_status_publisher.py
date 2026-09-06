import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "publish_status_snapshot", Path(__file__).parents[1] / "publish_status_snapshot.py"
)
publish_status_snapshot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = publish_status_snapshot
spec.loader.exec_module(publish_status_snapshot)
publish_if_new = publish_status_snapshot.publish_if_new


def snapshot(observed_at: datetime) -> dict:
    return {
        "schema_version": 1,
        "source": "local-event-record",
        "observed_at": observed_at.isoformat(),
        "received_at": observed_at.isoformat(),
        "items": [],
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
