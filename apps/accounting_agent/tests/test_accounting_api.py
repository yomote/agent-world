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


def test_final_phase_budget_includes_prior_attempts(monkeypatch, tmp_path) -> None:
    # 回帰: server再起動で過去18attemptを0へ戻し、追加枠を40回として扱わない。
    monkeypatch.setenv("ACCOUNTING_MODEL_CALL_LIMIT", "40")
    monkeypatch.setenv("ACCOUNTING_MODEL_CALLS_ALREADY_USED", "18")
    client = TestClient(create_app(tmp_path / "budget.sqlite3"))
    health = client.get("/healthz").json()
    assert health == {
        "status": "ok",
        "model_budget_limit": 40,
        "model_budget_used": 18,
        "model_budget_remaining": 22,
    }
