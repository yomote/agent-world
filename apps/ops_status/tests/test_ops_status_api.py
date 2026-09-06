import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from ops_status.api import create_app


def snapshot(received_at: datetime) -> dict:
    return {
        "schema_version": 1,
        "source": "pm-confirmed",
        "observed_at": received_at.isoformat(),
        "received_at": received_at.isoformat(),
        "items": [
            {
                "agent": "management-status-owner",
                "role": "実装担当",
                "task": "Issue #17 管理status最小版",
                "status": "running",
                "observed_at": received_at.isoformat(),
                "issue_url": "https://github.com/yomote/agent-world/issues/17",
            }
        ],
    }


def test_status_marks_old_received_snapshot_stale(tmp_path, monkeypatch):
    """更新が止まったsnapshotを現在稼働中に見せ続ける回帰を防ぐ。"""
    old = datetime.now(UTC) - timedelta(minutes=3)
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot(old)), encoding="utf-8")
    monkeypatch.setenv("AGENT_WORLD_STATUS_SNAPSHOT", str(path))

    response = TestClient(create_app()).get("/api/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["stale"] is True
    assert response.json()["age_seconds"] >= 180


def test_status_rejects_unknown_source(tmp_path, monkeypatch):
    """fixtureや手動snapshotをlive eventと誤表示する未知sourceを拒否する。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "live-ish"
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("AGENT_WORLD_STATUS_SNAPSHOT", str(path))

    response = TestClient(create_app()).get("/api/status")

    assert response.status_code == 503
    assert response.json()["detail"] == "status snapshot is invalid"


def test_status_page_is_served_from_same_origin():
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "connect-src 'self'" in response.text
    assert "api.github.com" not in response.text
