#!/usr/bin/env python3
"""正式 local merge 入口のproposal。CLI既定はdry-runである。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

REPOSITORY = "yomote/agent-world"
SHA = re.compile(r"^[0-9a-f]{40}$")


class Stop(RuntimeError):
    pass


@dataclass(frozen=True)
class Approval:
    packet_id: str
    pr_number: int
    expected_head: str
    trusted_source: str
    source_tree: str
    expected_login: str
    authority_receipt: str
    deploy_guard_sha: str
    execution_mode: str


@dataclass(frozen=True)
class Runtime:
    repository: str
    remote_main: str
    checkout_head: str
    checkout_tree: str
    clean: bool
    actor: str
    inherited_tokens: tuple[str, ...]
    deploy_enabled: bool | None
    deploy_guard_sha: str | None
    environment_approval_required: bool | None


@dataclass
class OperationReceipt:
    attempted: bool = False
    merge_result: str = "not_run"
    post_dispatch: str = "not_run"


class Adapter(Protocol):
    def inspect(self, expected_login: str) -> Runtime: ...

    def stored_token(self) -> str: ...


class SystemAdapter:
    """既存gh loginだけを使う実adapter。呼出しは明示--execute時に限る。"""

    def __init__(self, environment: dict[str, str] | None = None):
        self.environment = dict(os.environ if environment is None else environment)

    def _safe_env(self) -> dict[str, str]:
        environment = dict(self.environment)
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            environment.pop(name, None)
        environment["GITHUB_REPOSITORY"] = REPOSITORY
        return environment

    def _run(self, *args: str) -> str:
        try:
            return subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
                env=self._safe_env(),
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise Stop(f"preflight observation is unknown: {args[0]}") from error

    def inspect(self, expected_login: str) -> Runtime:
        inherited = tuple(
            name for name in ("GH_TOKEN", "GITHUB_TOKEN") if self.environment.get(name)
        )
        remote = self._run("git", "ls-remote", "origin", "refs/heads/main").split()
        if len(remote) != 2 or remote[1] != "refs/heads/main":
            raise Stop("remote main SHA is unavailable")
        guard = self._run("git", "show", "HEAD:.github/workflows/deploy-azure.yml")
        guard_sha = self._run("git", "rev-parse", "HEAD") if "DEPLOY_ENABLED" in guard else None
        deploy_enabled = (
            self._run("gh", "variable", "get", "AZURE_DEPLOY_ENABLED", "--repo", REPOSITORY)
            == "true"
        )
        rules = json.loads(
            self._run(
                "gh", "api", f"repos/{REPOSITORY}/environments/azure-production/protection-rules"
            )
        )
        total = rules.get("total_count") if isinstance(rules, dict) else None
        return Runtime(
            repository=REPOSITORY,
            remote_main=remote[0],
            checkout_head=self._run("git", "rev-parse", "HEAD"),
            checkout_tree=self._run("git", "rev-parse", "HEAD^{tree}"),
            clean=not self._run("git", "status", "--porcelain"),
            actor=self._run("gh", "api", "user", "--jq", ".login"),
            inherited_tokens=inherited,
            deploy_enabled=deploy_enabled,
            deploy_guard_sha=guard_sha,
            environment_approval_required=None if total is None else total > 0,
        )

    def stored_token(self) -> str:
        return self._run("gh", "auth", "token")


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise Stop(f"{field} must be a lowercase 40-character SHA")
    return value


def load_approval(raw: dict[str, Any]) -> Approval:
    """rootが固定した秘密なしpacketだけを受け入れる。"""

    expected = {
        "packet_id",
        "repository",
        "pr_number",
        "expected_head",
        "trusted_source",
        "source_tree",
        "expected_login",
        "authority_receipt",
        "review_url",
        "ci_url",
        "deploy_guard_sha",
        "deploy_enabled",
        "allow_merge",
        "allow_post_dispatch",
        "environment_approval_required",
        "execution_mode",
    }
    if set(raw) != expected:
        raise Stop("approval packet fields are incomplete or unexpected")
    if raw["repository"] != REPOSITORY or raw["allow_merge"] is not True:
        raise Stop("repository or merge approval is invalid")
    if raw["allow_post_dispatch"] is not False or raw["deploy_enabled"] is not False:
        raise Stop("dispatch and deployment must be prohibited")
    if raw["environment_approval_required"] is not False:
        raise Stop("environment approval must be explicitly absent")
    if raw["execution_mode"] not in {"normal", "bootstrap"}:
        raise Stop("execution mode is invalid")
    if not isinstance(raw["pr_number"], int) or raw["pr_number"] < 1:
        raise Stop("pr_number is invalid")
    for field in ("packet_id", "expected_login", "authority_receipt", "review_url", "ci_url"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise Stop(f"{field} is required")
    return Approval(
        raw["packet_id"],
        raw["pr_number"],
        _sha(raw["expected_head"], "expected_head"),
        _sha(raw["trusted_source"], "trusted_source"),
        _sha(raw["source_tree"], "source_tree"),
        raw["expected_login"],
        raw["authority_receipt"],
        _sha(raw["deploy_guard_sha"], "deploy_guard_sha"),
        raw["execution_mode"],
    )


def validate_runtime(approval: Approval, runtime: Runtime) -> None:
    """Git/gh/remote/environmentの実読取結果がpacketと一致しなければ停止する。"""

    if runtime.repository != REPOSITORY or runtime.inherited_tokens:
        raise Stop("repository or inherited token is invalid")
    if runtime.actor != approval.expected_login or not runtime.clean:
        raise Stop("stored gh login or clean checkout is invalid")
    if (
        runtime.remote_main != approval.trusted_source
        or runtime.checkout_head != approval.trusted_source
    ):
        raise Stop("remote main or checkout source mismatch")
    if runtime.checkout_tree != approval.source_tree:
        raise Stop("trusted tree mismatch")
    if runtime.deploy_enabled is not False or runtime.environment_approval_required is not False:
        raise Stop("deployment preflight is unknown or enabled")
    if runtime.deploy_guard_sha != approval.deploy_guard_sha:
        raise Stop("deployment guard is unknown or changed")
    if approval.execution_mode == "bootstrap" and not approval.authority_receipt.startswith(
        "https://"
    ):
        raise Stop("bootstrap authority receipt is not immutable evidence")


def execute_with_runner(
    approval: Approval,
    adapter: Adapter,
    runner: Callable[[str, int, str], str],
    receipt: OperationReceipt,
) -> OperationReceipt:
    """承認後に既存 strict gateへ繋ぐ一点。testsはfake runnerだけを渡す。"""

    if receipt.attempted:
        raise Stop("merge result is already known or unknown; retry is forbidden")
    runtime = adapter.inspect(approval.expected_login)
    validate_runtime(approval, runtime)
    token = adapter.stored_token()
    if not token:
        raise Stop("stored gh token is unavailable")
    receipt.attempted = True
    receipt.merge_result = runner(token, approval.pr_number, approval.expected_head)
    if receipt.merge_result not in {"merged", "failed", "unknown"}:
        raise Stop("strict gate receipt is invalid")
    return receipt


def run_existing_strict_gate(token: str, number: int, expected_head: str) -> str:
    """既存strict gateだけを呼び、local entryから後続dispatchを禁じる。"""

    sys.path.insert(0, str(Path(__file__).parent))
    import merge_gate

    client = merge_gate.GitHubClient(REPOSITORY, token)
    merge_gate.execute(
        client,
        merge_gate.GateTarget(number, expected_head),
        attempts=10,
        interval=60,
        dispatch_after_merge=False,
    )
    return "merged"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    approval = load_approval(json.loads(args.packet.read_text(encoding="utf-8")))
    if args.execute:
        receipt = execute_with_runner(
            approval, SystemAdapter(), run_existing_strict_gate, OperationReceipt()
        )
        print(json.dumps(receipt.__dict__, sort_keys=True))
        return 0
    print(f"approval packet {approval.packet_id} is valid; merge_result=not_run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
