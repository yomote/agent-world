"""Git記録待ちを承認解除と混同せず、旧試行と実内容の結合を検査する。"""

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))
from scripts.automation import delivery, helper_job  # noqa: E402
from scripts.automation.runner import Runner, digest, dispatcher  # noqa: E402
from scripts.automation.transport import Stop  # noqa: E402

OWNER = "01a07c68-367d-75e3-b267-3ae46db963ac"
PACKET = Path("artifacts/self-improvement/validation-attempt.json")


@pytest.fixture
def task(tmp_path):
    with dispatcher(tmp_path):
        store = Runner(tmp_path)
        task = delivery.Campaign(store, "billing-helper")
        task.init(OWNER, "b" * 40, seconds=1800)
        workspace = task.workspace()
        workspace.mkdir()
        delivery.git(workspace, "init")
        delivery.git(workspace, "config", "user.name", "Test")
        delivery.git(workspace, "config", "user.email", "test@example.invalid")
        delivery.git(workspace, "config", "core.autocrlf", "false")
        (workspace / "base.txt").write_text("base\n", encoding="utf-8")
        delivery.git(workspace, "add", "base.txt")
        delivery.git(workspace, "commit", "-m", "base")
        data = task.data()
        data["base"] = delivery.git(workspace, "rev-parse", "HEAD")
        task.save_event(data, "test_base")
        try:
            yield task
        finally:
            store.close()


def child_result(task):
    for path in delivery.SCOPES["billing-helper"]:
        target = task.workspace() / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((path + "\n").encode())
    outcome = dict(
        status="implementation_ready",
        base=task.data()["base"],
        refusal="none",
        files=[
            dict(path=path, sha256=sha) for path, sha in helper_job.hashes(task.workspace()).items()
        ],
        validation=[dict(check=check, exit_code=0) for check in helper_job.VALIDATIONS],
    )
    return save_completion(task, outcome)


def save_completion(task, outcome):
    message = json.dumps(outcome)
    task.enter()
    data = task.data()
    log = task.artifact_directory / "cli-1.jsonl"
    log.write_text("fixture CLI evidence\n", encoding="utf-8")
    data.update(cli_starts=1, job_owner=OWNER, cli_pid=None, cli_exit_code=0)
    data["cli_completion"] = dict(
        owner=OWNER,
        exit_code=0,
        turn_completed=True,
        command_failed=False,
        log=str(log.relative_to(task.root)),
        log_sha256=digest(log.read_bytes()),
        message_sha256=digest(message.encode()),
    )
    task.save_event(data, "cli_completed")
    return dict(owner=OWNER, message=message, exit_code=0)


def test_edits_record_reconcile_are_separate_and_bound(task):
    """編集receiptだけでSHA検証済みにし、任意の後付け成功を受け入れる回帰を防ぐ。"""
    before = task.data()
    result = child_result(task)
    helper_job.accept_implementation(task, result)
    assert task.data()["state"] == "environment_commit_required"
    assert task.data()["head"] is None
    with pytest.raises(Stop, match="successful_git_record_required"):
        helper_job.reconcile_commit(task)
    helper_job.record_commit(task)
    assert task.data()["state"] == "environment_commit_required"
    with pytest.raises(Stop, match="already_reserved"):
        helper_job.record_commit(task)
    helper_job.reconcile_commit(task)
    data = task.data()
    assert data["state"] == "job_verified"
    assert data["head"] != before["base"]
    assert delivery.git(task.workspace(), "rev-parse", "HEAD^") == before["base"]
    assert not delivery.git(task.workspace(), "status", "--porcelain")
    assert data["deadline"] == before["deadline"]
    assert data["http_requests"] == before["http_requests"] == 0
    assert data["cost_hardcap"] == "not_provided"


@pytest.mark.parametrize(
    "status", ["human_approval_required", "policy_denied", "permission_failed"]
)
def test_refusals_never_become_environment_commit_required(task, status):
    """人間承認・自動policy拒否・一般permission失敗を環境Git境界へ迂回させない。"""
    result = child_result(task)
    outcome = json.loads(result["message"])
    outcome["status"] = status
    result["message"] = json.dumps(outcome)
    with pytest.raises(Stop) as caught:
        helper_job.accept_implementation(task, result)
    task.finish_step(caught.value.state, caught.value.reason)
    old = task.data()
    for action in (helper_job.record_commit, helper_job.reconcile_commit):
        with pytest.raises(Stop, match="environment_commit_boundary"):
            action(task)
        assert task.data() == old


@pytest.mark.parametrize("change", ["hash", "path", "base", "validation", "refusal", "duplicate"])
def test_receipt_must_bind_exact_three_paths_and_validation(task, change):
    """schemaを満たすだけの誤hash・scope拡張・誤base・未検証を通さない。"""
    result = child_result(task)
    outcome = json.loads(result["message"])
    if change == "hash":
        outcome["files"][0]["sha256"] = "0" * 64
    elif change == "path":
        outcome["files"][0]["path"] = "docs/runbooks/azure-management-status.md"
    elif change == "base":
        outcome["base"] = "0" * 40
    elif change == "validation":
        outcome["validation"][0]["exit_code"] = 1
    elif change == "refusal":
        outcome["refusal"] = "policy"
    else:
        outcome["files"][1] = outcome["files"][0]
    # 実completionとのmessage結合も維持した上でreceipt自体の検査を要求する。
    task.finish_step("ready", "test_refined_receipt")
    result = save_completion(task, outcome)
    with pytest.raises(Stop):
        helper_job.accept_implementation(task, result)
    assert task.data()["head"] is None


@pytest.mark.parametrize("change", ["failed_command", "no_completion", "message", "log", "index"])
def test_cli_completion_and_unstaged_edits_are_required(task, change):
    """CLI欠落、失敗command、改変log、childによるindex操作を正常完了扱いしない。"""
    result = child_result(task)
    data = task.data()
    if change == "failed_command":
        data["cli_completion"]["command_failed"] = True
    elif change == "no_completion":
        data["cli_completion"]["turn_completed"] = False
    elif change == "message":
        result["message"] += " "
    elif change == "log":
        (task.artifact_directory / "cli-1.jsonl").write_text("changed", encoding="utf-8")
    else:
        delivery.git(task.workspace(), "add", "--", delivery.SCOPES["billing-helper"][0])
    with task.db:
        task.save(data)
    with pytest.raises(Stop):
        helper_job.accept_implementation(task, result)


@pytest.mark.parametrize("change", ["content", "extra_commit", "arbitrary_commit", "approval_wait"])
def test_reconcile_rejects_changes_or_unrecorded_commit(task, change):
    """Git記録後の別commit・内容変化やapproval_waitをreconcileで採用させない。"""
    helper_job.accept_implementation(task, child_result(task))
    if change == "arbitrary_commit":
        delivery.git(task.workspace(), "add", ".")
        delivery.git(task.workspace(), "commit", "-m", "arbitrary")
    else:
        helper_job.record_commit(task)
        if change == "content":
            (task.workspace() / delivery.SCOPES["billing-helper"][0]).write_text("changed")
        elif change == "extra_commit":
            delivery.git(task.workspace(), "commit", "--allow-empty", "-m", "extra")
        else:
            task.finish_step("approval_wait", "human_decision_required")
    with pytest.raises(Stop):
        helper_job.reconcile_commit(task)
    assert task.data()["state"] != "job_verified"


def prepare_old_attempt(task, monkeypatch, *, expired=False):
    data = task.data()
    data.update(
        base=helper_job.PRIOR_BASE,
        state="approval_wait",
        reason="helper_approval_wait",
        issue=28,
        duplicate_key=helper_job.DUPLICATE,
        job_owner=helper_job.PRIOR_OWNER,
        cli_exit_code=0,
        cli_pid=None,
        cli_starts=1,
        deadline=time.time() + (-1 if expired else 90),
    )
    task.save_event(data, "helper_approval_wait")
    with task.db:
        task.db.execute("UPDATE delivery_http_budget SET used=11 WHERE id=1")
        task.db.execute(
            "INSERT INTO delivery_campaigns VALUES ('runner',?)",
            (
                json.dumps(
                    {
                        "state": "merged",
                        "purpose": "commit_boundary",
                        "attempt_id": 2,
                        "merge_sha": "c" * 40,
                        "head": "d" * 40,
                    }
                ),
            ),
        )
    monkeypatch.setattr(helper_job, "committed_files", lambda *args: None)
    (task.root / PACKET).write_text(
        json.dumps(
            {
                "kind": "issue28-validation-v1",
                "seconds": 900,
                "issue": 28,
                "duplicate_key": helper_job.DUPLICATE,
                "owner": OWNER,
                "corrective_head": "d" * 40,
                "corrective_merge": "c" * 40,
                "scope": list(delivery.SCOPES["billing-helper"]),
            }
        ),
        encoding="utf-8",
    )
    return task.db.execute(
        "SELECT data FROM delivery_campaigns WHERE name='billing-helper'"
    ).fetchone()[0]


def test_explicit_attempt_freezes_old_failure_and_shares_budget(task, monkeypatch):
    """旧期限・失敗を凍結し、明示15分attemptでもIssue・累積回数をresetしない。"""
    raw = prepare_old_attempt(task, monkeypatch)
    old_events = list(task.db.execute("SELECT * FROM delivery_events"))
    helper_job.new_attempt(task, PACKET)
    current = task.data()
    archived = task.db.execute(
        "SELECT data,evidence FROM delivery_attempts WHERE campaign='billing-helper' AND attempt=1"
    ).fetchone()
    assert archived[0] == raw
    assert json.loads(archived[1])["prior_head"] == helper_job.PRIOR_HEAD
    assert json.loads(archived[0])["state"] == "approval_wait"
    assert current["attempt_id"] == 2 and current["state"] == "ready"
    assert current["issue"] == 28 and current["duplicate_key"] == helper_job.DUPLICATE
    assert current["head"] is None and current["cli_starts"] == 1
    assert current["prior_deadline"] == json.loads(raw)["deadline"]
    assert current["deadline"] == current["started_at"] + 900
    assert current["prior_started_at"] <= current["started_at"]
    assert current["http_requests"] == 11 and current["max_http_requests"] == 30
    assert current["max_connector_calls"] == 40
    assert current["max_delivery_attempts"] == current["max_cli_starts"] == 3
    assert current["campaign_seconds"] == 900
    assert task.workspace() == task.directory / "attempt-2/checkout"
    assert list(task.db.execute("SELECT * FROM delivery_events"))[: len(old_events)] == old_events
    with pytest.raises(sqlite3.IntegrityError, match="frozen_attempt"):
        task.db.execute("UPDATE delivery_attempts SET data='{}' WHERE attempt=1")
    with pytest.raises(Stop):
        helper_job.new_attempt(task, PACKET)
    assert task.db.execute("SELECT COUNT(*) FROM delivery_operations").fetchone()[0] == 0


def test_explicit_fifteen_minutes_stops_without_reset_or_old_attempt_extension(task, monkeypatch):
    """新旧期限を別保存し、新しい15分の超過を再起動や再packetで延長しない。"""
    raw = prepare_old_attempt(task, monkeypatch, expired=True)
    helper_job.new_attempt(task, PACKET)
    started = task.data()["started_at"]
    deadline = task.data()["deadline"]
    monkeypatch.setattr(delivery.time, "time", lambda: deadline + 1)
    with pytest.raises(Stop, match="campaign_time_budget"):
        task.enter()
    task.finish_step("stopped", "campaign_time_budget")
    assert task.data()["state"] == "stopped"
    assert task.data()["deadline"] == started + 900
    assert task.data()["prior_deadline"] == json.loads(raw)["deadline"]
    assert (
        task.db.execute(
            "SELECT data FROM delivery_attempts WHERE campaign='billing-helper' AND attempt=1"
        ).fetchone()[0]
        == raw
    )
    assert task.data()["cli_starts"] == 1 and task.data()["http_requests"] == 11
    assert not task.workspace().exists()
    with pytest.raises(Stop):
        helper_job.new_attempt(task, PACKET)
    with pytest.raises(Stop):
        delivery.Campaign(task, "billing-helper").enter()
    assert task.data()["deadline"] == deadline


def test_new_attempt_requires_corrective_normal_merge(task, monkeypatch):
    """レビュー済みでも未mergeのcorrective codeから実retryを始めさせない。"""
    raw = prepare_old_attempt(task, monkeypatch)
    with task.db:
        task.db.execute(
            "UPDATE delivery_campaigns SET data=? WHERE name='runner'",
            (json.dumps({"state": "job_verified"}),),
        )
    with pytest.raises(Stop):
        helper_job.new_attempt(task, PACKET)
    assert task.db.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0] == 0
    assert (
        task.db.execute(
            "SELECT data FROM delivery_campaigns WHERE name='billing-helper'"
        ).fetchone()[0]
        == raw
    )


@pytest.mark.parametrize("change", ["missing", "seconds", "scope", "head", "issue", "extra"])
def test_separate_explicit_packet_is_required_before_new_attempt(task, monkeypatch, change):
    """暗黙の30分付与・別scope・別head・別Issueのpacketで新attemptを作らない。"""
    raw = prepare_old_attempt(task, monkeypatch, expired=True)
    path = task.root / PACKET
    packet = json.loads(path.read_text())
    if change == "missing":
        path.unlink()
    else:
        key, value = {
            "seconds": ("seconds", 1800),
            "scope": ("scope", ["arbitrary.py"]),
            "head": ("corrective_head", "e" * 40),
            "issue": ("issue", 29),
            "extra": ("auto_approve", True),
        }[change]
        packet[key] = value
        path.write_text(json.dumps(packet))
    with pytest.raises((Stop, ValueError)):
        helper_job.new_attempt(task, PACKET)
    assert task.db.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0] == 0
    assert (
        task.db.execute(
            "SELECT data FROM delivery_campaigns WHERE name='billing-helper'"
        ).fetchone()[0]
        == raw
    )


def test_commit_record_reserves_once_before_failure(task, monkeypatch):
    """Git metadataの環境失敗をapproval_waitや無条件の再commit許可に変換しない。"""
    helper_job.accept_implementation(task, child_result(task))
    original = helper_job.git

    def denied(workspace, *args):
        if args[0] == "add":
            assert task.data()["git_record"]["state"] == "reserved"
            raise PermissionError("environment git metadata")
        return original(workspace, *args)

    monkeypatch.setattr(helper_job, "git", denied)
    with pytest.raises(PermissionError):
        helper_job.record_commit(task)
    with pytest.raises(Stop, match="already_reserved"):
        helper_job.record_commit(task)
    with pytest.raises(Stop, match="successful_git_record_required"):
        helper_job.reconcile_commit(task)


def test_retry_implementation_reuses_issue_and_edits_only_contract(task, monkeypatch):
    """別attemptの実装分岐が二つ目のIssueやchild Git記録を要求する回帰を防ぐ。"""
    prepare_old_attempt(task, monkeypatch)
    helper_job.new_attempt(task, PACKET)
    source = task.root / "source.json"
    source.write_text(
        json.dumps(
            {
                "debrief_id": "github:yomote/agent-world/pull/24:billing-debrief",
                "finding": "billing_evidence_normalization",
                "source_sha256": "1" * 64,
                "source_ref": "https://github.com/yomote/agent-world/pull/24",
            }
        ),
        encoding="utf-8",
    )

    def local_git(workspace, *args):
        if args[0] == "remote" and args[1] == "get-url":
            return "https://github.com/yomote/agent-world.git"
        if args[0] == "merge-base":
            return task.data()["base"]
        if args[0] == "clone":
            Path(args[-1]).mkdir()
        return ""

    def no_issue(*args, **kwargs):
        pytest.fail("existing Issue28 must be reused")

    def inspect_job(current, workspace, prompt, *, schema):
        assert workspace == task.directory / "attempt-2/checkout"
        assert current.data()["issue"] == 28
        assert current.data()["duplicate_key"] == helper_job.DUPLICATE
        assert "git add/commit/push" in prompt and "行わない" in prompt
        assert "head" not in schema["properties"]
        assert set(schema["required"]) == {"status", "base", "files", "validation", "refusal"}
        raise Stop("stopped", "fixture_job_boundary")

    monkeypatch.setattr(helper_job, "git", local_git)
    monkeypatch.setattr(helper_job.shutil, "copytree", lambda *args: None)
    monkeypatch.setattr(task.transport.github, "git_transfer", lambda *args: None)
    monkeypatch.setattr(task.transport, "call", no_issue)
    monkeypatch.setattr(helper_job, "run_job", inspect_job)
    with pytest.raises(Stop, match="fixture_job_boundary"):
        helper_job.implement(task, source)
    assert task.data()["retry_job_reserved"] is True
    assert task.data()["http_requests"] == 11
