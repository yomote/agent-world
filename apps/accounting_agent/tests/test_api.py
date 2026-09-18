from accounting_agent.api import create_app
from fastapi.testclient import TestClient


def test_baseline_api_exposes_server_assigned_mode_and_durable_replay(tmp_path) -> None:
    # 回帰: client提供principalを信頼せず、server内の固定capabilityでrunを進める。
    client = TestClient(create_app(tmp_path / "runs.sqlite3"))
    started = client.post("/api/accounting/runs", json={"mode": "baseline"})
    assert started.status_code == 200
    run_id = started.json()["run_id"]
    latest = started.json()
    for _ in range(10):
        latest = client.post(
            f"/api/accounting/runs/{run_id}/advance",
            json={
                "action_id": f"action-{latest['step_version']}",
                "expected_step_version": latest["step_version"],
            },
        ).json()
        if latest["status"] == "ready_for_review":
            break
    assert latest["status"] == "ready_for_review"
    assert latest["artifact"]["payload"]["actual_ledger_updated"] is False
    replay = client.get(f"/api/accounting/runs/{run_id}")
    assert replay.status_code == 200
    assert replay.json()["artifact"]["digest"] == latest["artifact"]["digest"]
