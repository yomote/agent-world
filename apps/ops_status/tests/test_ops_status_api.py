import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from ops_status.api import create_app
from ops_status.models import StatusSnapshot


class MemoryStore:
    def __init__(self, value: StatusSnapshot | None = None):
        self.value = value
        self.write_count = 0

    def read(self) -> StatusSnapshot:
        if self.value is None:
            raise FileNotFoundError
        return self.value

    def write(self, value: StatusSnapshot) -> None:
        self.value = value
        self.write_count += 1


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


@pytest.mark.parametrize("expected_upper", [False, True])
def test_azure_auth_separates_operator_reads_from_ingest_writes(monkeypatch, expected_upper):
    """同じOIDのcase差による拒否と、本人/ingestの権限逆転を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "local-event-record"
    store = MemoryStore(StatusSnapshot.model_validate(data))
    client = TestClient(create_app(store))
    monkeypatch.setenv("AGENT_WORLD_STATUS_REQUIRE_AUTH", "true")
    operator = "11111111-1111-4111-8111-aaaaaaaaaaaa"
    ingest = "22222222-2222-4222-8222-bbbbbbbbbbbb"
    expected_operator = operator.upper() if expected_upper else operator
    expected_ingest = ingest.upper() if expected_upper else ingest
    actual_operator = operator if expected_upper else operator.upper()
    actual_ingest = ingest if expected_upper else ingest.upper()
    monkeypatch.setenv("AGENT_WORLD_STATUS_OPERATOR_OBJECT_ID", expected_operator)
    monkeypatch.setenv("AGENT_WORLD_STATUS_INGEST_OBJECT_ID", expected_ingest)

    assert client.get("/", headers={"x-ms-client-principal-id": actual_ingest}).status_code == 403
    assert client.get("/", headers={"x-ms-client-principal-id": actual_operator}).status_code == 200
    assert (
        client.get("/api/status", headers={"x-ms-client-principal-id": actual_operator}).status_code
        == 200
    )
    assert (
        client.put(
            "/api/status",
            headers={"x-ms-client-principal-id": actual_operator},
            json=data,
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/status",
            headers={"x-ms-client-principal-id": actual_ingest},
            json=data,
        ).status_code
        == 204
    )


@pytest.mark.parametrize(
    "expected,actual",
    [("", ""), ("invalid", "invalid"), ("11111111-1111-4111-8111-aaaaaaaaaaaa", "invalid")],
)
def test_azure_auth_rejects_invalid_principal_ids(monkeypatch, expected, actual):
    """GUID正規化で空欄や一致する不正OIDまで認可する回帰を防ぐ。"""
    monkeypatch.setenv("AGENT_WORLD_STATUS_REQUIRE_AUTH", "true")
    monkeypatch.setenv("AGENT_WORLD_STATUS_OPERATOR_OBJECT_ID", expected)
    client = TestClient(create_app(MemoryStore()))

    assert client.get("/", headers={"x-ms-client-principal-id": actual}).status_code == 403


def test_ingest_does_not_rewrite_blob_for_same_observation():
    """同じeventの再送で保存時刻やBlobを更新し続ける回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "local-event-record"
    store = MemoryStore(StatusSnapshot.model_validate(data))

    response = TestClient(create_app(store)).put("/api/status", json=data)

    assert response.status_code == 204
    assert store.write_count == 0


def test_ingest_keeps_state_transition_for_same_source_observation():
    """同じtask event時刻でactivityがstaleへ変わったsnapshotを落とす回帰を防ぐ。"""
    current = datetime.now(UTC)
    data = snapshot(current)
    data["source"] = "local-event-record"
    store = MemoryStore(StatusSnapshot.model_validate(data))
    changed = snapshot(current)
    changed["source"] = "local-event-record"
    changed["received_at"] = (current + timedelta(seconds=1)).isoformat()
    changed["items"][0].update(
        {
            "status": "unknown",
            "latest_activity": "structured-item",
            "latest_activity_at": current.isoformat(),
            "stale": True,
        }
    )

    response = TestClient(create_app(store)).put("/api/status", json=changed)

    assert response.status_code == 204
    assert store.write_count == 1
    assert store.value.items[0].status == "unknown"
    assert store.value.items[0].stale is True


def test_ingest_rejects_manual_snapshot():
    """手動snapshotを外部runtime eventとして公開する回帰を防ぐ。"""
    response = TestClient(create_app(MemoryStore())).put(
        "/api/status", json=snapshot(datetime.now(UTC))
    )

    assert response.status_code == 422
