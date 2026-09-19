import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from accounting_agent.store import RunStore


def test_sqlite_history_replays_public_trace_without_hidden_reasoning(tmp_path) -> None:
    # 回帰: 再現証拠を永続化しつつhidden chain-of-thoughtを保存しない。
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run("run-1", "agent", "known")
    store.record_attempt("run-1")
    store.record_model_result("run-1", True)
    store.append_trace(
        "run-1",
        "decision",
        "read_document",
        {"short_public_reason": "入金連絡を確認", "args": {"document_id": "DOC-MAIL"}},
        decision_id="decision-1",
    )
    store.save_artifact("package-1", "run-1", 1, "review_package", {"status": "valid"}, "abc")
    replay = store.get_run("run-1")
    assert replay is not None
    assert replay["status"] == "ready_for_review"
    assert replay["model_attempts"] == 1
    assert replay["trace"][0]["payload"]["short_public_reason"] == "入金連絡を確認"
    assert "chain_of_thought" not in str(replay)


def test_artifact_publication_is_idempotent_only_for_identical_payload(tmp_path) -> None:
    # 回帰: 応答不明後の同一publicationは増殖せず、別payloadのID再利用を拒否する。
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run("run-1", "agent", "known")
    store.save_artifact("package-1", "run-1", 1, "review_package", {"value": 1}, "abc")
    store.save_artifact("package-1", "run-1", 1, "review_package", {"value": 1}, "abc")
    replay = store.get_run("run-1")
    assert replay is not None and replay["artifact"]["payload"] == {"value": 1}
    try:
        store.save_artifact("package-1", "run-1", 1, "review_package", {"value": 2}, "def")
    except ValueError as error:
        assert str(error) == "artifact_id_conflict"
    else:
        raise AssertionError("conflicting artifact ID must be rejected")


def test_operation_receipt_replays_exact_request_and_rejects_conflict(tmp_path) -> None:
    # 回帰: double clickと応答喪失でmodel decisionを二重発行せず、ID使い回しを拒否する。
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run("run-1", "agent", "known")
    assert store.claim_operation("run-1", "action-1", "advance", 0, {"run_id": "run-1"}) == "new"
    store.complete_operation("action-1", "run-1")
    assert store.claim_operation("run-1", "action-1", "advance", 0, {"run_id": "run-1"}) == "replay"
    try:
        store.claim_operation("run-1", "action-1", "advance", 1, {"run_id": "run-1"})
    except ValueError as error:
        assert str(error) == "action_id_conflict"
    else:
        raise AssertionError("same action ID with a different step must conflict")


def test_pending_operation_becomes_terminal_unknown_after_restart(tmp_path) -> None:
    # 回帰: crash後のpending decisionを新Actionで無差別再送しない。
    path = tmp_path / "runs.sqlite3"
    first = RunStore(path)
    first.create_run("run-1", "agent", "known")
    assert first.claim_operation("run-1", "action-1", "advance", 0, {"run_id": "run-1"}) == "new"
    restarted = RunStore(path)
    assert (
        restarted.claim_operation("run-1", "action-2", "advance", 0, {"run_id": "run-1"})
        == "unknown"
    )
    assert restarted.get_run("run-1")["status"] == "unknown_terminal"


def test_concurrent_distinct_actions_claim_only_one_model_step(tmp_path) -> None:
    # 回帰: 同じstepへの同時clickがSELECT→INSERT競合を抜けて二重model callにならない。
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run("run-1", "agent", "known")
    barrier = Barrier(2)

    def claim(action_id: str) -> str:
        barrier.wait()
        return store.claim_operation("run-1", action_id, "advance", 0, {"run_id": "run-1"})

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(claim, ["action-a", "action-b"]))

    assert sorted(outcomes) == ["in_progress", "new"]


def test_store_migrates_pre_budget_run_history(tmp_path) -> None:
    # 回帰: 旧ローカル履歴DBを捨てず、追加したstep/budget列を安全に補う。
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE runs (
             run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, status TEXT NOT NULL,
             fixture_id TEXT NOT NULL, created_at TEXT NOT NULL,
             model_attempts INTEGER NOT NULL DEFAULT 0,
             model_successes INTEGER NOT NULL DEFAULT 0,
             model_failures INTEGER NOT NULL DEFAULT 0
           )"""
    )
    connection.commit()
    connection.close()
    store = RunStore(path)
    store.create_run("run-new", "baseline", "fixture-new")
    assert store.get_run("run-new")["step_version"] == 0
