"""proposal local entryが認証混線や未知のdeploy状態で実mergeしないことを検証する。"""

import importlib.util
import json
import sys
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


def packet(**overrides):
    result = {
        "packet_id": "approval-1",
        "repository": "yomote/agent-world",
        "pr_number": 91,
        "expected_head": HEAD,
        "trusted_source": SHA,
        "source_tree": SHA,
        "expected_login": "owner",
        "authority_receipt": "https://example.invalid/approval",
        "allow_merge": True,
        "allow_post_dispatch": False,
        "review_url": "https://example.invalid/review",
        "ci_url": "https://example.invalid/ci",
        "deploy_guard_sha": SHA,
        "deploy_enabled": False,
        "environment_approval_required": False,
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
        "clean": True,
        "actor": "owner",
        "inherited_tokens": (),
        "deploy_enabled": False,
        "deploy_guard_sha": SHA,
        "environment_approval_required": False,
    }
    result.update(overrides)
    return local.Runtime(**result)


def test_packet_rejects_deploy_enabled_and_dispatch():
    """deploy可能または後続dispatchを許すpacketでmergeする回帰を防ぐ。"""
    for broken in ({"deploy_enabled": True}, {"allow_post_dispatch": True}):
        with pytest.raises(local.Stop):
            local.load_approval(packet(**broken))


def test_runtime_rejects_repo_actor_token_and_source_mixing():
    """別repo・別actor・親token・stale sourceをtrusted実行と誤認しない。"""
    approval = local.load_approval(packet())
    for broken in (
        {"repository": "other/repo"},
        {"actor": "other"},
        {"inherited_tokens": ("GH_TOKEN",)},
        {"remote_main": HEAD},
        {"checkout_head": HEAD},
        {"clean": False},
    ):
        with pytest.raises(local.Stop):
            local.validate_runtime(approval, runtime(**broken))


def test_fake_runner_receipt_has_no_dispatch_and_unknown_is_not_retried():
    """fakeだけがrunnerを呼び、unknownでもpost-dispatchや再送をしない。"""
    approval = local.load_approval(packet())
    calls = []

    class Adapter:
        def inspect(self, expected_login):
            assert expected_login == "owner"
            return runtime()

        def stored_token(self):
            return "not-printed"

    receipt = local.execute_with_runner(
        approval,
        Adapter(),
        lambda token, number, head: calls.append((token, number, head)) or "unknown",
        local.OperationReceipt(),
    )
    assert calls == [("not-printed", 91, HEAD)]
    assert (receipt.merge_result, receipt.post_dispatch) == ("unknown", "not_run")
    with pytest.raises(local.Stop, match="retry"):
        local.execute_with_runner(approval, Adapter(), lambda *args: "merged", receipt)


def test_cli_execute_connects_only_after_fake_adapter_preflight(tmp_path, monkeypatch):
    """明示executeはadapter検証後だけstrict gateへ接続する。"""
    source = tmp_path / "approval.json"
    source.write_text(json.dumps(packet()), encoding="utf-8")

    class Adapter:
        def inspect(self, expected_login):
            assert expected_login == "owner"
            return runtime()

        def stored_token(self):
            return "not-printed"

    calls = []
    monkeypatch.setattr(local, "SystemAdapter", Adapter)
    monkeypatch.setattr(
        local,
        "run_existing_strict_gate",
        lambda token, number, head: calls.append((token, number, head)) or "merged",
    )
    assert local.main([str(source), "--execute"]) == 0
    assert calls == [("not-printed", 91, HEAD)]


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
