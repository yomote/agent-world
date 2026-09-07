"""外部操作の予約、再送防止、ownerとcurrent evidenceの境界を検証する。"""

import io
import json
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))
from scripts.automation import bridge, delivery, transport  # noqa: E402
from scripts.automation.runner import Runner, dispatcher  # noqa: E402

OWNER = "01a07c68-367d-75e3-b267-3ae46db963ac"
HEAD = "a" * 40


def test_live_relay_fail_closed_contract():
    """relayの実行順序・unknown停止・正式review保留をCIでも検査する。"""
    subprocess.run(
        ["node", str(Path(__file__).with_name("test_self_improvement_relay.mjs"))], check=True
    )


@pytest.fixture
def task(tmp_path):
    with dispatcher(tmp_path):
        runner = Runner(tmp_path)
        job = delivery.Campaign(runner, "runner")
        job.init(OWNER, "b" * 40)
        job.enter()
        yield job
        runner.close()


def test_transport_reserves_before_delivery_and_claim_is_once(task, monkeypatch):
    """transportが操作後にstateを埋めたり、再起動で同じwriteを再送する回帰を防ぐ。"""
    write = transport.atomic_json

    def relay(path, request):
        write(path, request)
        if path.name != "request.json":
            return
        row = task.db.execute(
            "SELECT state FROM delivery_operations WHERE id=?", (request["id"],)
        ).fetchone()
        assert row[0] == "inflight"
        claimed = bridge.take(task.root, task.name)
        assert claimed == request
        assert bridge.take(task.root, task.name) is None
        write(
            path.parent / (request["id"] + ".response.json"),
            {
                "id": request["id"],
                "status": "ok",
                "result": {"number": 25},
            },
        )

    monkeypatch.setattr(transport, "atomic_json", relay)
    assert task.transport.call("current_check", {}, write=True) == {"number": 25}
    assert task.data()["connector_calls"] == 1
    assert bridge.take(task.root, task.name) is None


def test_unknown_write_and_approval_response_stop_without_retry(task, monkeypatch):
    """承認要求・不明な外部writeを自動回答や別経路で再送する回帰を防ぐ。"""
    write = transport.atomic_json

    def denied(path, request):
        write(path, request)
        if path.name != "request.json":
            return
        write(
            path.parent / (request["id"] + ".response.json"),
            {
                "id": request["id"],
                "status": "approval_wait",
                "result": {},
            },
        )

    monkeypatch.setattr(transport, "atomic_json", denied)
    with pytest.raises(transport.Stop) as caught:
        task.transport.call("current_check", {}, write=True)
    task.finish_step(caught.value.state, caught.value.reason)
    assert task.data()["state"] == "approval_wait"
    with pytest.raises(transport.Stop):
        task.enter()
    assert task.data()["connector_calls"] == 1


def test_request_timeout_does_not_accept_later_receipt(task):
    """外部writeの時間切れを未実行とし、遅れたreceiptで別実行を始める回帰を防ぐ。"""
    with pytest.raises(transport.Stop, match="no_replay") as caught:
        task.transport.call("current_check", {}, write=True, seconds=0)
    task.finish_step(caught.value.state, caught.value.reason)
    assert task.data()["state"] == "unknown"
    assert bridge.take(task.root, task.name) is None
    with pytest.raises(transport.Stop):
        task.enter()


def test_connector_cap_and_deadline_survive_restart(task):
    """再起動で共有呼出予算をresetし、費用hardcapを有ると偽る回帰を防ぐ。"""
    data = task.data()
    data["connector_calls"] = data["max_connector_calls"]
    task.save_event(data, "test_exhausted")
    with pytest.raises(transport.Stop, match="connector_call_budget"):
        task.transport.call("pr_info", {})
    with pytest.raises(ValueError, match="no_budget_reset"):
        task.init(OWNER, HEAD)
    assert task.data()["cost_hardcap"] == "not_provided"
    data["deadline"] = time.time() - 1
    task.save_event(data, "test_deadline")
    with pytest.raises(transport.Stop, match="time_budget"):
        task.boundary()


def test_expired_owner_is_unknown_and_never_replays(task):
    """process終了後のlease切れが二重起動の許可に変わる回帰を防ぐ。"""
    data = task.data()
    data["lease_until"] = time.time() - 1
    task.save_event(data, "test_expired")
    successor = delivery.Campaign(
        type("RunnerRef", (), {"db": task.db, "root": task.root})(), "runner"
    )
    with pytest.raises(transport.Stop, match="expired_owner"):
        successor.enter()
    assert successor.data()["state"] == "unknown"
    assert successor.data()["token"] is None


def test_result_identity_cannot_be_substituted(task, monkeypatch):
    """他要求のPASSを現在の外部操作の結果として消費する回帰を防ぐ。"""
    write = transport.atomic_json

    def wrong(path, request):
        if path.name != "request.json":
            return
        write(
            path.parent / (request["id"] + ".response.json"),
            {
                "id": "wrong",
                "status": "ok",
                "result": {"merged": True},
            },
        )

    monkeypatch.setattr(transport, "atomic_json", wrong)
    with pytest.raises(transport.Stop, match="identity_mismatch"):
        task.transport.call("current_check", {}, write=True)


def test_latest_failed_ci_does_not_reuse_old_success(task, monkeypatch):
    """同headの新attempt失敗やDraft skipを古いCI成功で上書きする回帰を防ぐ。"""
    runs = [
        dict(
            head_sha=HEAD,
            event="pull_request",
            pull_requests=[{"number": 25}],
            path=".github/workflows/ci.yml",
            status="completed",
            conclusion=status,
            run_number=1,
            run_attempt=attempt,
            id=attempt,
        )
        for attempt, status in [(1, "success"), (2, "skipped")]
    ]
    monkeypatch.setattr(
        task.transport,
        "call",
        lambda *args, **kwargs: {"workflow_runs": runs, "total_count": len(runs)},
    )
    with pytest.raises(transport.Stop, match="not_success"):
        task.wait_ci(HEAD, 25)
    assert task.data()["ci_queries"][HEAD]["count"] == 1


def test_other_pr_ci_never_passes_current_pr(task, monkeypatch):
    """別PRで同headの成功を今回のPRへ流用する回帰を防ぐ。"""
    calls = []

    def answer(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise transport.Stop("stopped", "test_no_current_pr_run")
        return {
            "total_count": 1,
            "workflow_runs": [
                {
                    "head_sha": HEAD,
                    "event": "pull_request",
                    "path": ".github/workflows/ci.yml",
                    "pull_requests": [{"number": 24}],
                    "status": "completed",
                    "conclusion": "success",
                    "id": 1,
                    "run_number": 1,
                    "run_attempt": 1,
                }
            ],
        }

    monkeypatch.setattr(task.transport, "call", answer)
    monkeypatch.setattr(delivery.time, "sleep", lambda _: task.data())
    data = task.data()
    data["ci_queries"][HEAD] = {"count": 9, "last_at": 0}
    task.save_event(data, "test_budget")
    with pytest.raises(transport.Stop, match="ci_query_budget"):
        task.wait_ci(HEAD, 25)
    assert "ci" not in task.data()


def test_helper_requires_parent_merge_before_any_issue(task, monkeypatch, tmp_path):
    """runner未mergeでhelperのIssueやjobが作られる回帰を防ぐ。"""
    from scripts.automation import helper_job

    child = delivery.Campaign(
        type("RunnerRef", (), {"db": task.db, "root": task.root})(), "billing-helper"
    )
    child.init(OWNER, HEAD)
    with pytest.raises(transport.Stop, match="runner_merge_required"):
        helper_job.implement(child, tmp_path / "missing.json")
    assert child.data()["connector_calls"] == 0


def test_merged_runner_initializes_helper_with_new_persistent_thirty_minutes(tmp_path, monkeypatch):
    """実CLI分岐でhelperに2時間を継承し、再起動で期限や共有予算をresetする回帰を防ぐ。"""
    from scripts.automation import helper_job, jobs

    now = [1000.0]
    monkeypatch.setattr(delivery.time, "time", lambda: now[0])
    with dispatcher(tmp_path), closing(Runner(tmp_path)) as store:
        parent = delivery.Campaign(store, "runner")
        parent.init(OWNER, "b" * 40)
        with store.db:
            store.db.execute("UPDATE delivery_http_budget SET used=17 WHERE id=1")

    def merged(task, workspace):
        assert task.name == "runner"
        now[0] = 2000.0
        data = task.data()
        data.update(state="merged", merge_sha=HEAD)
        task.save_event(data, "test_parent_merge")

    def expire_helper(task, source):
        assert task.name == "billing-helper"
        data = task.data()
        assert data["deadline"] == 3800.0
        assert data["campaign_seconds"] == 1800
        assert data["max_cli_starts"] == data["max_delivery_attempts"] == 3
        assert data["http_requests"] == 17 and data["max_http_requests"] == 30
        assert data["max_connector_calls"] == 40
        assert data["cost_hardcap"] == "not_provided"
        assert data["model_request_hardcap"] == "not_provided"
        with pytest.raises(ValueError, match="dispatcher_busy"), dispatcher(tmp_path):
            pytest.fail("second concurrent dispatcher")
        with pytest.raises(ValueError, match="no_budget_reset"):
            task.init(OWNER, HEAD, seconds=1800)
        task.enter()
        exhausted = task.data()
        exhausted["cli_starts"] = 3
        task.save_event(exhausted, "test_cli_exhausted")
        with pytest.raises(transport.Stop, match="cli_start_budget"):
            jobs.run_job(task, tmp_path, "must not launch")
        now[0] = 3800.0
        task.boundary()

    monkeypatch.setattr(delivery, "ROOT", tmp_path)
    monkeypatch.setattr(delivery.Campaign, "deliver", merged)
    monkeypatch.setattr(helper_job, "implement", expire_helper)
    monkeypatch.setattr(
        sys, "argv", ["delivery", "runner", "deliver", "--helper-source", "unused.json"]
    )
    assert delivery.main() == 2
    with closing(Runner(tmp_path)) as store:
        helper = delivery.Campaign(store, "billing-helper")
        data = helper.data()
        assert data["state"] == "stopped" and data["reason"] == "campaign_time_budget"
        assert data["debrief"]["outcome"] == "stopped" and data["token"] is None
        assert data["deadline"] == 3800.0 and data["http_requests"] == 17
        assert data["cli_starts"] == 3
        with pytest.raises(transport.Stop, match="campaign_time_budget"):
            helper.enter()
        with pytest.raises(ValueError, match="no_budget_reset"):
            helper.init(OWNER, HEAD, seconds=1800)
        assert helper.data() == data


def test_helper_delivery_stops_before_fourth_attempt_and_rejects_old_packet(task, monkeypatch):
    """known failの修正で3試行上限を戻し、表現不能な旧packetで実行する回帰を防ぐ。"""
    helper = delivery.Campaign(task, "billing-helper")
    with pytest.raises(ValueError, match="invalid_campaign_budget"):
        helper.init(OWNER, HEAD, seconds=7200)
    helper.init(OWNER, HEAD, seconds=1800)
    workspace = helper.directory / "checkout"
    current = ["c" * 40]
    calls = []

    def checkout_git(path, *args):
        if args[0] == "remote":
            return "https://github.com/yomote/agent-world.git"
        return current[0] if args[0] == "rev-parse" else helper.data()["head"]

    def failed_review(operation, *args, **kwargs):
        assert operation == "independent_review"
        calls.append(operation)
        raise transport.Stop("failed", "independent_review_not_pass")

    monkeypatch.setattr(delivery, "git", checkout_git)
    monkeypatch.setattr(helper, "check_scope", lambda *args, **kwargs: None)
    monkeypatch.setattr(helper.transport, "call", failed_review)
    data = helper.data()
    data.update(state="job_verified", head=current[0])
    helper.save_event(data, "test_job_verified")
    for attempt in range(1, 4):
        with pytest.raises(transport.Stop, match="independent_review_not_pass") as caught:
            helper.deliver(workspace)
        helper.finish_step(caught.value.state, caught.value.reason)
        assert helper.data()["delivery_attempts"] == attempt
        current[0] = str(attempt) * 40
        helper.revise(workspace)
        assert helper.data()["deadline"] == data["deadline"]
    with pytest.raises(transport.Stop, match="helper_delivery_attempt_budget") as caught:
        helper.deliver(workspace)
    helper.finish_step(caught.value.state, caught.value.reason)
    assert len(calls) == 3
    assert helper.data()["delivery_attempts"] == 3
    with pytest.raises(transport.Stop, match="helper_delivery_attempt_budget"):
        delivery.Campaign(task, "billing-helper").enter()

    # 旧schemaを安全な初回とみなしてcounterを0へ補完しない。
    data = helper.data()
    data.update(state="job_verified", token=None)
    del data["delivery_attempts"]
    helper.save_event(data, "test_old_packet")
    with pytest.raises(transport.Stop, match="helper_packet_not_representable"):
        helper.enter()
    assert helper.data()["state"] == "stopped"


def test_unprotected_main_never_reaches_merge(task, monkeypatch):
    """mainの保護確認不能を通常保護merge成功と混同する回帰を防ぐ。"""
    calls = []

    def answer(operation, arguments, **kwargs):
        calls.append(operation)
        return {"protected": False}

    monkeypatch.setattr(task.transport, "call", answer)
    with pytest.raises(transport.Stop, match="main_protection_unverified"):
        task.normal_merge(HEAD, 25)
    assert calls == ["main_protection"]


def test_draft_skipped_job_is_not_workflow_success(task, monkeypatch):
    """Draftでworkflow=success/job=skippedを実check成功に変換する回帰を防ぐ。"""

    def answer(operation, arguments, **kwargs):
        if operation == "current_ci":
            return {
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": 5,
                        "head_sha": HEAD,
                        "event": "pull_request",
                        "pull_requests": [{"number": 25}],
                        "path": ".github/workflows/ci.yml",
                        "run_number": 1,
                        "run_attempt": 1,
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            }
        return {
            "total_count": 1,
            "jobs": [
                {
                    "id": 6,
                    "name": "check",
                    "head_sha": HEAD,
                    "status": "completed",
                    "conclusion": "skipped",
                }
            ],
        }

    monkeypatch.setattr(task.transport, "call", answer)
    data = task.data()
    data["ci_queries"][HEAD] = {"count": 9, "last_at": 0}
    task.save_event(data, "test_last_query")
    with pytest.raises(transport.Stop, match="ci_query_budget"):
        task.wait_ci(HEAD, 25)
    assert "ci" not in task.data()


def test_stale_review_cannot_reach_merge(task, monkeypatch):
    """reviewコメント削除・古いheadの宣言をmerge直前の証拠に流用する回帰を防ぐ。"""
    calls = []
    answers = {
        "main_protection": {"protected": True},
        "merge_snapshot": {
            "pr": {
                "number": 25,
                "head": {"sha": HEAD, "repo": {"full_name": "yomote/agent-world"}},
                "draft": False,
                "state": "open",
                "mergeable": True,
                "base": {"ref": "main", "repo": {"full_name": "yomote/agent-world"}},
            },
            "comments": [],
            "threads": [],
        },
    }

    def answer(operation, arguments, **kwargs):
        calls.append(operation)
        return answers[operation]

    monkeypatch.setattr(task.transport, "call", answer)
    with pytest.raises(transport.Stop, match="current_review_unverified"):
        task.normal_merge(HEAD, 25)
    assert "normal_merge" not in calls


def test_no_receipt_import_command_or_bypass_option():
    """実CLI起動を省き、承認bypassや既成結果のimportへ戻す回帰を防ぐ。"""
    from scripts.automation import jobs

    source = Path(jobs.__file__).read_text(encoding="utf-8")
    assert '"on-request"' in source
    assert '"workspace-write"' in source
    assert "--approve-for-me" not in source
    assert "--dangerously-bypass" not in source
    assert "--ignore-rules" not in source


def test_known_failure_revision_preserves_budget_but_unknown_cannot_resume(task, monkeypatch):
    """修正後の再reviewでbudgetを戻し、unknown writeまで再開する回帰を防ぐ。"""
    data = task.data()
    data.update(
        state="failed",
        reason="independent_review_not_pass",
        head=HEAD,
        connector_calls=7,
        token=None,
        lease_until=None,
    )
    task.save_event(data, "test_failed")
    monkeypatch.setattr(
        delivery, "git", lambda root, *args: "c" * 40 if args[0] == "rev-parse" else HEAD
    )
    monkeypatch.setattr(task, "check_scope", lambda *args, **kwargs: None)
    task.revise(task.root)
    assert task.data()["state"] == "job_verified"
    assert task.data()["connector_calls"] == 7
    task.finish_step("unknown", "merge_result_unknown")
    with pytest.raises(transport.Stop, match="revision_not_allowed"):
        task.revise(task.root)


def test_only_interrupted_read_only_review_can_resume(task, monkeypatch):
    """承認待ちやunknown writeをread-only review再開に偽装する回帰を防ぐ。"""
    identifier = "11111111-1111-4111-8111-111111111111"
    data = task.data()
    data.update(state="job_verified", head=HEAD, lease_until=time.time() - 1)
    task.save_event(data, "head_fixed")
    with task.db:
        task.db.execute(
            "INSERT INTO delivery_operations VALUES (?,?,?,'inflight',?,NULL)",
            (
                identifier,
                task.name,
                "independent_review",
                json.dumps({"arguments": {"head": HEAD, "workspace": str(task.root)}}),
            ),
        )
    monkeypatch.setattr(delivery, "git", lambda *args: HEAD)
    monkeypatch.setattr(task, "check_scope", lambda *args, **kwargs: None)
    task.resume_review(identifier)
    assert task.data()["token"] is None
    assert task.data()["http_requests"] == 0
    assert (
        task.db.execute(
            "SELECT state FROM delivery_operations WHERE id=?", (identifier,)
        ).fetchone()[0]
        == "cancelled_read_only"
    )
    task.finish_step("unknown", "write_unknown")
    with pytest.raises(transport.Stop, match="safe_review_resume_not_confirmed"):
        task.resume_review(identifier)
    assert task.data()["debrief"]["outcome"] == "unknown"


def test_helper_review_recovery_uses_bound_checkout_and_is_atomic(task, monkeypatch):
    """helperをrootで検証する誤りと、取消しだけcommitされる中断回帰を防ぐ。"""
    helper = delivery.Campaign(task, "billing-helper")
    helper.init(OWNER, "b" * 40)
    helper.enter()
    workspace = helper.directory / "checkout"
    workspace.mkdir()
    identifier = "22222222-2222-4222-8222-222222222222"
    data = helper.data()
    data.update(state="job_verified", head=HEAD, lease_until=time.time() - 1)
    helper.save_event(data, "head_fixed")
    before = helper.data()

    def pending(path):
        with helper.db:
            helper.db.execute(
                "INSERT OR REPLACE INTO delivery_operations VALUES (?,?,?,'inflight',?,NULL)",
                (
                    identifier,
                    helper.name,
                    "independent_review",
                    json.dumps({"arguments": {"head": HEAD, "workspace": str(path)}}),
                ),
            )

    pending(task.root)
    with pytest.raises(transport.Stop, match="review_resume_workspace_mismatch"):
        helper.resume_review(identifier)
    pending(workspace)

    def checkout_git(path, *args):
        assert path == workspace
        return "c" * 40 if args[0] == "rev-parse" else HEAD

    def checkout_scope(path, head, *, base):
        assert (path, head, base) == (workspace, "c" * 40, HEAD)

    monkeypatch.setattr(delivery, "git", checkout_git)
    monkeypatch.setattr(helper, "check_scope", checkout_scope)
    original_save = helper.save

    def interrupted_save(data):
        assert (
            helper.db.execute(
                "SELECT state FROM delivery_operations WHERE id=?", (identifier,)
            ).fetchone()[0]
            == "cancelled_read_only"
        )
        raise RuntimeError("injected_interruption_after_cancellation")

    monkeypatch.setattr(helper, "save", interrupted_save)
    with pytest.raises(RuntimeError, match="injected_interruption"):
        helper.resume_review(identifier)
    assert helper.data() == before
    assert (
        helper.db.execute(
            "SELECT state FROM delivery_operations WHERE id=?", (identifier,)
        ).fetchone()[0]
        == "inflight"
    )
    monkeypatch.setattr(helper, "save", original_save)
    helper.resume_review(identifier)
    after = helper.data()
    assert after["head"] == "c" * 40
    assert after["token"] is None
    for key in ("deadline", "connector_calls", "http_requests", "cli_starts"):
        assert after[key] == before[key]
    assert (
        helper.db.execute(
            "SELECT state FROM delivery_operations WHERE id=?", (identifier,)
        ).fetchone()[0]
        == "cancelled_read_only"
    )


@pytest.mark.parametrize(
    "events,reason",
    [
        ([{"type": "turn.completed"}], "cli_start_event_missing"),
        ([{"type": "thread.started", "thread_id": OWNER}], "cli_completion_event_missing"),
    ],
)
def test_missing_cli_lifecycle_events_are_not_success(task, monkeypatch, events, reason):
    """exit 0だけで起動・完了event漏れを成功扱いする回帰を防ぐ。fixtureはE2Eではない。"""
    from scripts.automation import jobs

    class Child:
        pid = 1
        stdin = io.BytesIO()
        stdout = io.BytesIO(("\n".join(json.dumps(event) for event in events) + "\n").encode())

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(jobs.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *args, **kwargs: Child())
    with pytest.raises(transport.Stop, match=reason):
        jobs.run_job(task, task.root, "fixed", read_only=True)
