"""proposal local entryが認証混線や未知のdeploy状態で実mergeしないことを検証する。"""

import base64
import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "trusted_local_merge", Path(__file__).parents[1] / "trusted_local_merge.py"
)
local = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = local
spec.loader.exec_module(local)

SHA = "a" * 40
HEAD = "b" * 40
COMMENT = "https://github.com/yomote/agent-world/issues/44#issuecomment-123"
REVIEW = "https://github.com/yomote/agent-world/pull/91#issuecomment-456"
CI = "https://github.com/yomote/agent-world/actions/runs/789"


def packet(**overrides):
    result = {
        "packet_id": "approval-1",
        "repository": "yomote/agent-world",
        "pr_number": 91,
        "expected_head": HEAD,
        "trusted_source": SHA,
        "source_tree": SHA,
        "expected_login": "owner",
        "authority_receipt": COMMENT,
        "allow_merge": True,
        "allow_post_dispatch": False,
        "review_url": REVIEW,
        "ci_url": CI,
        "expires_at": "2099-01-01T00:00:00+00:00",
        "execution_mode": "normal",
    }
    result.update(overrides)
    return result


def runtime(**overrides):
    result = {
        "repository": "yomote/agent-world",
        "remote_main": SHA,
        "checkout_head": SHA,
        "checkout_tree": SHA,
        "detached": True,
        "clean": True,
        "actor": "owner",
        "inherited_tokens": (),
        "candidate_workflow_tree_matches_trusted": True,
        "trusted_deploy_blob_is_audited": True,
        "candidate_deploy_blob_is_audited": True,
        "candidate_deploy_matches_trusted": True,
        "candidate_has_known_deploy_guard": True,
        "deploy_enabled": False,
        "environment_approval_required": False,
    }
    result.update(overrides)
    return local.Runtime(**result)


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def inspect(self, approval):
        self.calls.append(("inspect", approval.packet_id))
        return runtime()

    def verify_evidence(self, approval):
        self.calls.append(("evidence", approval.expected_head))

    def stored_token(self):
        self.calls.append(("token",))
        return "not-printed"


def test_packet_rejects_expired_unbound_and_post_dispatch():
    """期限切れ又は任意URLだけのpacketでmergeする回帰を防ぐ。"""
    for broken in (
        {"expires_at": "2000-01-01T00:00:00+00:00"},
        {"authority_receipt": "x"},
        {"allow_post_dispatch": True},
    ):
        with pytest.raises(local.Stop):
            local.load_approval(packet(**broken))


def test_runtime_rejects_repo_actor_token_source_deploy_and_attached_mixing():
    """別repo・別actor・親token・stale source・deploy不明をtrusted実行と誤認しない。"""
    approval = local.load_approval(packet())
    for broken in (
        {"repository": "other/repo"},
        {"actor": "other"},
        {"inherited_tokens": ("GH_TOKEN",)},
        {"remote_main": HEAD},
        {"checkout_head": HEAD},
        {"clean": False},
        {"detached": False},
        {"candidate_deploy_matches_trusted": False},
        {"candidate_workflow_tree_matches_trusted": False},
        {"trusted_deploy_blob_is_audited": False},
        {"candidate_deploy_blob_is_audited": False},
        {"candidate_has_known_deploy_guard": False},
        {"deploy_enabled": True},
        {"environment_approval_required": None},
    ):
        with pytest.raises(local.Stop):
            local.validate_runtime(approval, runtime(**broken))


def test_persistent_receipt_forbids_restart_after_unknown(tmp_path):
    """unknownを記録した後のprocess再起動でも再PUTしない。"""
    approval = local.load_approval(packet())
    adapter = FakeAdapter()
    calls = []
    receipt_path = tmp_path / "receipt.json"
    receipt = local.execute_with_runner(
        approval,
        adapter,
        lambda token, approved: calls.append((token, approved.pr_number)) or "unknown",
        local.ReceiptStore(receipt_path),
    )
    assert calls == [("not-printed", 91)]
    assert receipt.merge_result == "unknown"
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["merge_result"] == "unknown"
    with pytest.raises(local.Stop, match="retry"):
        local.execute_with_runner(
            approval, adapter, lambda *args: "merged", local.ReceiptStore(receipt_path)
        )
    assert adapter.calls == [
        ("inspect", "approval-1"),
        ("evidence", HEAD),
        ("token",),
        ("inspect", "approval-1"),
        ("evidence", HEAD),
    ]


def test_cli_execute_connects_only_after_fake_preflight_and_receipt(tmp_path, monkeypatch):
    """明示executeはevidenceと原子的receipt予約後だけstrict gateへ接続する。"""
    source = tmp_path / "approval.json"
    source.write_text(json.dumps(packet()), encoding="utf-8")
    adapter = FakeAdapter()
    calls = []
    monkeypatch.setattr(local, "SystemAdapter", lambda: adapter)
    monkeypatch.setattr(
        local,
        "run_existing_strict_gate",
        lambda token, approved: calls.append((token, approved.pr_number)) or "merged",
    )
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(local, "receipt_path_for", lambda approval: receipt)
    assert local.main([str(source), "--execute"]) == 0
    assert calls == [("not-printed", 91)]
    assert json.loads(receipt.read_text(encoding="utf-8"))["post_dispatch"] == "not_run"


def test_system_adapter_removes_parent_tokens_before_gh_calls(monkeypatch):
    """stored gh auth以外の親tokenをsubprocessへ渡す回帰を防ぐ。"""
    adapter = local.SystemAdapter({"GH_TOKEN": "secret", "GITHUB_TOKEN": "secret"})
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs["env"])
        return type("Result", (), {"stdout": "owner\n"})()

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    assert adapter.stored_token() == "owner"
    assert "GH_TOKEN" not in seen and "GITHUB_TOKEN" not in seen
    assert seen["GITHUB_REPOSITORY"] == "yomote/agent-world"


def test_system_adapter_checks_candidate_workflow_from_expected_head(monkeypatch):
    """古いmainだけを見てcandidateのpush deployを見逃す回帰を防ぐ。"""
    adapter = local.SystemAdapter()
    workflow = b"""\
jobs:
  deploy:
    if: github.ref == 'refs/heads/main'
    steps:
      - env:
          DEPLOY_ENABLED: ${{ vars.AZURE_DEPLOY_ENABLED }}
        run: test "${DEPLOY_ENABLED}" = "true"
      - name: Sign in to Azure with OIDC
"""
    responses = {
        ("git", "ls-remote", "origin", "refs/heads/main"): f"{SHA}\trefs/heads/main",
        ("git", "show", f"{SHA}:.github/workflows/deploy-azure.yml"): workflow.decode(),
        (
            "git",
            "ls-tree",
            "-r",
            SHA,
            "--",
            ".github/workflows",
        ): f"100644 blob {local.AUDITED_DEPLOY_BLOB}\t.github/workflows/deploy-azure.yml",
        ("git", "rev-parse", "HEAD"): SHA,
        ("git", "rev-parse", "HEAD^{tree}"): SHA,
        ("git", "branch", "--show-current"): "",
        ("git", "status", "--porcelain"): "",
        ("gh", "api", "user", "--jq", ".login"): "owner",
    }

    def fake_run(*args):
        if args == (
            "gh",
            "api",
            f"repos/yomote/agent-world/contents/.github/workflows/deploy-azure.yml?ref={HEAD}",
        ):
            return json.dumps(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(workflow).decode(),
                    "sha": local.AUDITED_DEPLOY_BLOB,
                }
            )
        if args == ("gh", "api", f"repos/yomote/agent-world/git/trees/{HEAD}?recursive=1"):
            return json.dumps(
                {
                    "truncated": False,
                    "tree": [
                        {
                            "path": ".github/workflows/deploy-azure.yml",
                            "type": "blob",
                            "sha": local.AUDITED_DEPLOY_BLOB,
                        }
                    ],
                }
            )
        if args == (
            "gh",
            "api",
            "repos/yomote/agent-world/environments/azure-production/protection-rules",
        ):
            return json.dumps({"total_count": 0})
        if args == ("gh", "api", "repos/yomote/agent-world/actions/variables?per_page=100"):
            return json.dumps({"total_count": 0, "variables": []})
        return responses[args]

    monkeypatch.setattr(adapter, "_run", fake_run)
    observed = adapter.inspect(local.load_approval(packet()))
    assert observed.candidate_deploy_matches_trusted is True
    assert observed.candidate_has_known_deploy_guard is True
    assert observed.deploy_enabled is False
    assert observed.candidate_workflow_tree_matches_trusted is True


def test_system_adapter_binds_review_ci_and_authority_to_packet(monkeypatch):
    """見かけだけのURLをroot approval・review・CIの根拠にしない。"""
    adapter = local.SystemAdapter()
    approval = local.load_approval(packet())
    responses = {
        "repos/yomote/agent-world/issues/comments/456": {
            "body": f"{local.REVIEW_MARKER}\nhead: {HEAD}\nverdict: pass"
        },
        "repos/yomote/agent-world/actions/runs/789": {
            "head_sha": HEAD,
            "conclusion": "success",
            "event": "pull_request",
            "pull_requests": [{"number": 91}],
        },
        "repos/yomote/agent-world/issues/comments/123": {
            "body": "\n".join(
                (
                    local.APPROVAL_MARKER,
                    "packet: approval-1",
                    f"trusted_source: {SHA}",
                    f"expected_head: {HEAD}",
                    "pr_number: 91",
                    "execution_mode: normal",
                    f"expires_at: {approval.expires_at.isoformat()}",
                )
            ),
            "user": {"login": "owner"},
        },
    }
    monkeypatch.setattr(adapter, "_api_json", lambda path: responses[path])
    adapter.verify_evidence(approval)


def test_variable_listing_allows_absent_but_rejects_incomplete(monkeypatch):
    """404等を不在と誤認せず、完全な一覧でだけmissingをdisabledと扱う。"""
    adapter = local.SystemAdapter()
    monkeypatch.setattr(adapter, "_api_json", lambda path: {"total_count": 0, "variables": []})
    assert adapter._deployment_enabled() is False
    monkeypatch.setattr(adapter, "_api_json", lambda path: {"total_count": 101, "variables": []})
    with pytest.raises(local.Stop, match="incomplete"):
        adapter._deployment_enabled()


def test_strict_gate_is_loaded_from_fixed_git_object(monkeypatch):
    """preflight後のdirty worktreeをstrict gateとして実行する回帰を防ぐ。"""
    source = """\
class GitHubClient:
    def __init__(self, repository, token):
        assert repository == 'yomote/agent-world'
        assert token == 'token'
    def put(self, path, payload):
        assert path == '/repos/yomote/agent-world/pulls/91/merge'
        return {'merged': True, 'sha': 'c' * 40}
class GateTarget:
    def __init__(self, number, head):
        assert (number, head) == (91, 'b' * 40)
def execute(client, target, attempts, interval, dispatch_after_merge):
    assert attempts == 10 and interval == 60 and dispatch_after_merge is False
"""

    def fake_run(args, **kwargs):
        assert args == ("git", "show", f"{SHA}:scripts/merge_gate.py")
        assert "GH_TOKEN" not in kwargs["env"] and "GITHUB_TOKEN" not in kwargs["env"]
        return type("Result", (), {"stdout": source.encode("utf-8")})()

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    assert local.run_existing_strict_gate("token", local.load_approval(packet())) == "merged"


def test_real_trusted_gate_module_loads_utf8_dataclasses_without_network():
    """Windows既定codepageに依存せず実trusted sourceをimportできる。"""
    module = local._trusted_gate_module("031288738cd2efe5d929060926ffd1470afd3133")
    assert module.GateTarget(91, HEAD).number == 91


def test_canonical_receipt_ignores_packet_id_and_caller_path(monkeypatch, tmp_path):
    """同じauthorityのpacket_id変更や任意pathでoperation receiptを回避しない。"""
    monkeypatch.setattr(local, "RECEIPT_ROOT", tmp_path)
    first = local.load_approval(packet(packet_id="one"))
    second = local.load_approval(packet(packet_id="two"))
    assert local.receipt_path_for(first) == local.receipt_path_for(second)


def test_expiry_guard_stops_before_merge_put_after_wait():
    """CI待機後に期限切れならPUTを発行しない。"""
    expired = replace(local.load_approval(packet()), expires_at=datetime(2000, 1, 1, tzinfo=UTC))
    calls = []
    guarded = local._expiry_guarded_put(expired, lambda *args: calls.append(args))
    with pytest.raises(local.Stop, match="expired"):
        guarded("/repos/yomote/agent-world/pulls/91/merge", {"sha": HEAD})
    assert calls == []
