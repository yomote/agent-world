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
