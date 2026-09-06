"""merge gateが古い・不完全な証跡でmergeしないことを検証する。"""

import importlib.util
import io
import sys
import urllib.error
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "merge_gate", Path(__file__).parents[1] / "merge_gate.py"
)
merge_gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = merge_gate
spec.loader.exec_module(merge_gate)

SHA = "a" * 40
OTHER_SHA = "b" * 40


def pr_data(**overrides):
    result = {
        "number": 7,
        "state": "open",
        "draft": False,
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
        "head": {"sha": SHA, "repo": {"full_name": "owner/repo"}},
        "labels": [],
        "mergeable": True,
        "node_id": "PR_node",
        "auto_merge": {"enabled_by": {"login": "owner"}},
    }
    result.update(overrides)
    return result


def protected_ruleset():
    return {
        "enforcement": "active",
        "target": "branch",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "rules": [
            {"type": "deletion", "parameters": {}},
            {"type": "non_fast_forward", "parameters": {}},
            {"type": "required_linear_history", "parameters": {}},
            {
                "type": "pull_request",
                "parameters": {"required_review_thread_resolution": True},
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": "check"}],
                },
            },
        ],
    }


def test_pr_head_draft_base_and_labels_fail_closed():
    """古いreview、Draft、対象外base、停止labelでmergeする回帰を防ぐ。"""
    target = merge_gate.GateTarget(7, SHA)
    merge_gate.validate_pr(pr_data(), target)
    for broken in (
        pr_data(draft=True),
        pr_data(head={"sha": OTHER_SHA, "repo": {"full_name": "owner/repo"}}),
        pr_data(base={"ref": "release", "repo": {"full_name": "owner/repo"}}),
        pr_data(labels=[{"name": "needs-human"}]),
        pr_data(mergeable=None),
    ):
        with pytest.raises(merge_gate.GateError):
            merge_gate.validate_pr(broken, target)


def test_review_requires_exact_current_sha_and_trusted_author():
    """レビュー書式への言及、旧head、第三者コメントを証跡と誤認しない。"""
    valid_body = (
        f"{merge_gate.REVIEW_MARKER}\nhead: {SHA}\nverdict: pass\nreviewer: sol-reviewer\n補足"
    )
    comments = [
        {"body": f"例: {valid_body}", "author_association": "OWNER"},
        {
            "body": valid_body.replace(SHA, OTHER_SHA),
            "author_association": "OWNER",
        },
        {"body": valid_body, "author_association": "NONE"},
    ]
    with pytest.raises(merge_gate.GateError):
        merge_gate.validate_review(comments, SHA)
    comments.append({"body": valid_body, "author_association": "OWNER"})
    assert merge_gate.validate_review(comments, SHA) == "sol-reviewer"
    comments.append(
        {
            "body": valid_body.replace("verdict: pass", "verdict: fail"),
            "author_association": "OWNER",
        }
    )
    with pytest.raises(merge_gate.GateError, match="rejects"):
        merge_gate.validate_review(comments, SHA)


def test_ci_requires_success_for_same_pr_and_head():
    """Draftのskippedや別PR・旧headのsuccessをPASSにする回帰を防ぐ。"""
    target = merge_gate.GateTarget(7, SHA)
    skipped = {
        "head_sha": SHA,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "skipped",
        "pull_requests": [{"number": 7}],
        "run_number": 10,
        "run_attempt": 1,
        "id": 100,
    }
    with pytest.raises(merge_gate.GateError):
        merge_gate.validate_ci([skipped], target)
    success = skipped | {"conclusion": "success"}
    assert merge_gate.validate_ci([success], target)
    assert not merge_gate.validate_ci([success | {"status": "in_progress"}], target)
    assert not merge_gate.validate_ci([success | {"head_sha": OTHER_SHA}], target)

    # 同じSHAに古いsuccessがあっても、最新attemptの失敗を隠さない。
    old_success = success | {"run_number": 10, "run_attempt": 1, "id": 100}
    new_failure = skipped | {
        "conclusion": "failure",
        "run_number": 10,
        "run_attempt": 2,
        "id": 101,
    }
    with pytest.raises(merge_gate.GateError):
        merge_gate.validate_ci([old_success, new_failure], target)


def test_ruleset_requires_strict_check_threads_and_no_bypass():
    """GitHub保護が未適用・緩和された状態でmergeする回帰を防ぐ。"""
    merge_gate.validate_ruleset([protected_ruleset()])
    for mutate in ("bypass", "unknown-bypass", "exclude", "strict", "thread", "context"):
        ruleset = protected_ruleset()
        if mutate == "bypass":
            ruleset["bypass_actors"] = [{"actor_type": "RepositoryRole"}]
        elif mutate == "unknown-bypass":
            ruleset.pop("bypass_actors")
        elif mutate == "exclude":
            ruleset["conditions"]["ref_name"]["exclude"] = ["refs/heads/ma*"]
        elif mutate == "strict":
            ruleset["rules"][4]["parameters"]["strict_required_status_checks_policy"] = False
        elif mutate == "thread":
            ruleset["rules"][3]["parameters"]["required_review_thread_resolution"] = False
        else:
            ruleset["rules"][4]["parameters"]["required_status_checks"] = [
                {"context": "self-written-status"}
            ]
        with pytest.raises(merge_gate.GateError):
            merge_gate.validate_ruleset([ruleset])


def test_cli_enforces_bounded_ci_polling():
    """外部APIを短間隔または上限なしでpollする設定を許さない。"""
    args = merge_gate.parse_args(["7", SHA])
    assert (args.ci_attempts, args.ci_interval) == (10, 60)
    for argv in (["7", SHA, "--ci-attempts", "11"], ["7", SHA, "--ci-interval", "59"]):
        with pytest.raises(SystemExit):
            merge_gate.parse_args(argv)


def test_privileged_workflow_has_only_explicit_main_dispatch():
    """PRコード・コメント・scheduleから特権merge処理が起動する回帰を防ぐ。"""
    workflow = (Path(__file__).parents[2] / ".github/workflows/merge-gate.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "pull_request:" not in workflow
    assert "issue_comment:" not in workflow
    assert "schedule:" not in workflow


def test_execute_uses_expected_sha_once_and_dispatches_verified_merge(monkeypatch):
    """最終検査後のmergeをexpected SHA付き1回に限定し、後続へ結果を渡す。"""

    class Client:
        def __init__(self):
            self.repository = "owner/repo"
            self.put_calls = []

        def put(self, path, body):
            self.put_calls.append((path, body))
            return {"merged": True, "sha": OTHER_SHA}

    monkeypatch.setattr(merge_gate, "evaluate", lambda *args, **kwargs: "sol-reviewer")
    monkeypatch.setattr(merge_gate, "wait_for_ci", lambda *args, **kwargs: None)
    dispatched = []
    monkeypatch.setattr(
        merge_gate,
        "dispatch_post_merge",
        lambda client, target, merge_sha: dispatched.append((target, merge_sha)),
    )
    client = Client()
    reviewer = merge_gate.execute(
        client,
        merge_gate.GateTarget(7, SHA),
        attempts=1,
        interval=60,
        dispatch_after_merge=True,
    )
    assert reviewer == "sol-reviewer"
    assert client.put_calls == [
        ("/repos/owner/repo/pulls/7/merge", {"sha": SHA, "merge_method": "squash"})
    ]
    assert dispatched == [(merge_gate.GateTarget(7, SHA), OTHER_SHA)]


def test_execute_does_not_retry_unknown_merge_write(monkeypatch):
    """merge結果不明時は再送せず、read-only確認1回で未確認なら止める。"""

    class Client:
        def __init__(self):
            self.repository = "owner/repo"
            self.put_count = 0
            self.get_count = 0

        def get(self, path):
            self.get_count += 1
            return pr_data(auto_merge=None, merged=False)

        def put(self, path, body):
            self.put_count += 1
            raise merge_gate.GitHubUnknownError("unknown")

    monkeypatch.setattr(merge_gate, "evaluate", lambda *args, **kwargs: "sol-reviewer")
    monkeypatch.setattr(merge_gate, "wait_for_ci", lambda *args, **kwargs: None)
    client = Client()
    with pytest.raises(merge_gate.GateError, match="remains unknown"):
        merge_gate.execute(client, merge_gate.GateTarget(7, SHA), attempts=1, interval=60)
    assert (client.put_count, client.get_count) == (1, 1)


def test_post_merge_dispatch_contract_contains_verified_shas():
    """main CIとdeployへ渡すmerge結果のSHA契約が欠落する回帰を防ぐ。"""

    class Client:
        repository = "owner/repo"

        def __init__(self):
            self.calls = []

        def post(self, path, body):
            self.calls.append((path, body))

    client = Client()
    target = merge_gate.GateTarget(7, SHA)
    merge_gate.dispatch_post_merge(client, target, OTHER_SHA)
    assert client.calls == [
        ("/repos/owner/repo/actions/workflows/ci.yml/dispatches", {"ref": "main"}),
        (
            "/repos/owner/repo/dispatches",
            {
                "event_type": "agent-world-merged",
                "client_payload": {
                    "pr_number": 7,
                    "head_sha": SHA,
                    "merge_commit_sha": OTHER_SHA,
                },
            },
        ),
    ]


def test_uncertain_write_http_error_is_unknown_but_known_rejection_is_not(monkeypatch):
    """書込み408/429/5xxだけを結果不明にし、既知403やread失敗と混同しない。"""

    def fail_with(code):
        def urlopen(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, code, "failure", {}, io.BytesIO(b"failure")
            )

        return urlopen

    client = merge_gate.GitHubClient("owner/repo", "token")
    for code in (408, 429, 502):
        monkeypatch.setattr(merge_gate.urllib.request, "urlopen", fail_with(code))
        with pytest.raises(merge_gate.GitHubUnknownError):
            client.put("/write", {})
    monkeypatch.setattr(merge_gate.urllib.request, "urlopen", fail_with(403))
    with pytest.raises(merge_gate.GitHubError) as known:
        client.put("/write", {})
    assert not isinstance(known.value, merge_gate.GitHubUnknownError)
    monkeypatch.setattr(merge_gate.urllib.request, "urlopen", fail_with(502))
    with pytest.raises(merge_gate.GitHubError) as read_failure:
        client.get("/read")
    assert not isinstance(read_failure.value, merge_gate.GitHubUnknownError)


def test_post_merge_failure_reports_irreversible_partial_state():
    """merge済みなのに後続失敗を未mergeと誤読する回帰を防ぐ。"""

    class Client:
        repository = "owner/repo"

        def __init__(self, fail_at):
            self.fail_at = fail_at
            self.calls = 0

        def post(self, path, body):
            self.calls += 1
            if self.calls == self.fail_at:
                raise merge_gate.GitHubUnknownError("unknown")

    target = merge_gate.GateTarget(7, SHA)
    with pytest.raises(merge_gate.GateError, match="main CI dispatch=unknown.*not_run"):
        merge_gate.dispatch_post_merge(Client(1), target, OTHER_SHA)
    with pytest.raises(merge_gate.GateError, match="main CI dispatch=sent.*azure dispatch=unknown"):
        merge_gate.dispatch_post_merge(Client(2), target, OTHER_SHA)
