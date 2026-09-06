from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from world.api import create_app


def action(**updates):
    return {"action_id": str(uuid4()), "actor_id": "A", "type": "move", "dx": 1, "dy": 0, **updates}


def test_http_action_event_and_observation():
    """実HTTP境界でActionと確定Stateがつながらない配線ミスを防ぐ。"""
    with TestClient(create_app()) as client:
        initial = client.get("/api/world").json()
        payload = action()
        response = client.post("/api/actions", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["event"]["action"] == payload
        assert result["event"]["status"] == "success"
        assert result["event"]["before"] == initial["entities"][0]["position"]
        observed = client.get("/api/world")
        assert observed.json() == result["world"]
        assert observed.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "updates",
    [
        {"type": "teleport"},
        {"dx": True},
        {"dx": "1"},
        {"dx": 0.5},
        {"world": {}},
        {"action_id": "bad"},
    ],
)
def test_invalid_payload_is_rejected_without_mutation(updates):
    """型の暗黙変換やStateの持ち込みがAction検証をすり抜ける回帰を防ぐ。"""
    with TestClient(create_app()) as client:
        before = client.get("/api/world").json()
        assert client.post("/api/actions", json=action(**updates)).status_code == 422
        assert client.get("/api/world").json() == before


def test_rule_failure_is_an_event_and_direct_write_is_unavailable():
    """ルール上の拒否を通信エラーと混同せず、Stateの直接更新口を作らない。"""
    with TestClient(create_app()) as client:
        before = client.get("/api/world").json()
        response = client.post("/api/actions", json=action(dx=10))
        assert response.status_code == 200
        assert response.json()["event"]["status"] == "failure"
        assert response.json()["world"] == before
        assert client.put("/api/world", json=before).status_code == 405
