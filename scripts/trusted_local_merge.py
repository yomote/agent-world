#!/usr/bin/env python3
"""正式 local merge 入口のproposal。CLI既定はdry-runである。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

REPOSITORY = "yomote/agent-world"
AUDITED_DEPLOY_BLOB = "70b36a8328a2a9551cb5338519bfe6381546cb28"
RECEIPT_ROOT = Path.home() / ".codex" / "agent-world-operation-receipts"
SHA = re.compile(r"^[0-9a-f]{40}$")
COMMENT_URL = re.compile(
    r"^https://github\.com/yomote/agent-world/(issues|pull)/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)$"
)
RUN_URL = re.compile(r"^https://github\.com/yomote/agent-world/actions/runs/([1-9][0-9]*)$")
REVIEW_MARKER = "<!-- agent-world-independent-review -->"
APPROVAL_MARKER = "<!-- agent-world-local-merge-approval -->"


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
    review_url: str
    ci_url: str
    expires_at: datetime
    execution_mode: str


@dataclass(frozen=True)
class Runtime:
    repository: str
    remote_main: str
    checkout_head: str
    checkout_tree: str
    detached: bool
    clean: bool
    actor: str
    inherited_tokens: tuple[str, ...]
    candidate_workflow_tree_matches_trusted: bool | None
    trusted_deploy_blob_is_audited: bool | None
    candidate_deploy_blob_is_audited: bool | None
    candidate_deploy_matches_trusted: bool | None
    candidate_has_known_deploy_guard: bool | None
    deploy_enabled: bool | None
    environment_approval_required: bool | None


@dataclass(frozen=True)
class OperationReceipt:
    repository: str
    packet_id: str
    pr_number: int
    trusted_source: str
    expected_head: str
    execution_mode: str
    authority_receipt: str
    merge_result: str
    post_dispatch: str = "not_run"


class Adapter(Protocol):
    def inspect(self, approval: Approval) -> Runtime: ...

    def verify_evidence(self, approval: Approval) -> None: ...

    def stored_token(self) -> str: ...


class ReceiptStore:
    """一度でもmergeを試みたpacketを再起動後もterminalとして扱う。"""

    def __init__(self, path: Path):
        self.path = path

    def reserve(self, approval: Approval) -> OperationReceipt:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        receipt = OperationReceipt(
            REPOSITORY,
            approval.packet_id,
            approval.pr_number,
            approval.trusted_source,
            approval.expected_head,
            approval.execution_mode,
            approval.authority_receipt,
            "in_progress",
        )
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise Stop(
                "merge operation was already attempted or is unknown; retry is forbidden"
            ) from error
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(asdict(receipt), output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
        except Exception as error:
            # A partially recorded reservation is still terminal: never remove it automatically.
            raise Stop("operation receipt reservation is unknown; retry is forbidden") from error
        return receipt

    def finish(self, receipt: OperationReceipt, merge_result: str) -> OperationReceipt:
        if merge_result not in {"merged", "failed", "unknown"}:
            raise Stop("strict gate receipt is invalid")
        final = OperationReceipt(
            receipt.repository,
            receipt.packet_id,
            receipt.pr_number,
            receipt.trusted_source,
            receipt.expected_head,
            receipt.execution_mode,
            receipt.authority_receipt,
            merge_result,
        )
        temporary = self.path.with_name(f".{self.path.name}.tmp-{os.getpid()}")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(asdict(final), output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            raise Stop(
                "merge result is unknown; receipt persistence failed and retry is forbidden"
            ) from error
        return final


def _comment(value: str, field: str) -> tuple[str, int, str]:
    match = COMMENT_URL.fullmatch(value)
    if not match:
        raise Stop(f"{field} must be an agent-world issue or pull comment URL")
    return match.group(1), int(match.group(2)), match.group(3)


def _comment_id(value: str, field: str) -> str:
    return _comment(value, field)[2]


def _review_comment(value: str) -> tuple[int, str]:
    kind, number, comment_id = _comment(value, "review_url")
    if kind != "pull":
        raise Stop("review_url must identify the target pull request")
    return number, comment_id


def _run_id(value: str) -> str:
    match = RUN_URL.fullmatch(value)
    if not match:
        raise Stop("ci_url must be an agent-world Actions run URL")
    return match.group(1)


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
                args, check=True, capture_output=True, text=True, env=self._safe_env()
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as error:
            raise Stop(f"preflight observation is unknown: {args[0]}") from error

    def _run_bytes(self, *args: str) -> bytes:
        """git objectの内容は末尾改行を含む真正bytesのまま読む。"""
        try:
            return subprocess.run(
                args, check=True, capture_output=True, env=self._safe_env()
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise Stop(f"preflight observation is unknown: {args[0]}") from error

    def _api_json(self, path: str) -> Any:
        try:
            return json.loads(self._run("gh", "api", path))
        except json.JSONDecodeError as error:
            raise Stop("preflight API response is unknown") from error

    @staticmethod
    def _workflow_tree(entries: Any) -> dict[str, str] | None:
        if not isinstance(entries, dict) or entries.get("truncated") is not False:
            return None
        tree = entries.get("tree")
        if not isinstance(tree, list):
            return None
        result: dict[str, str] = {}
        for item in tree:
            if not isinstance(item, dict):
                return None
            path, kind, blob = item.get("path"), item.get("type"), item.get("sha")
            if isinstance(path, str) and path.startswith(".github/workflows/"):
                if kind != "blob" or not isinstance(blob, str):
                    return None
                result[path] = blob
        return result

    def _deployment_enabled(self) -> bool:
        """完全に取得できたrepository variablesだけからguard値を読む。"""
        response = self._api_json(f"repos/{REPOSITORY}/actions/variables?per_page=100")
        if not isinstance(response, dict):
            raise Stop("deployment variable listing is unknown")
        total, variables = response.get("total_count"), response.get("variables")
        if not isinstance(total, int) or total < 0 or not isinstance(variables, list):
            raise Stop("deployment variable listing is unknown")
        if total != len(variables):
            raise Stop("deployment variable listing is incomplete")
        matches = [
            item
            for item in variables
            if isinstance(item, dict) and item.get("name") == "AZURE_DEPLOY_ENABLED"
        ]
        if not matches:
            # The audited shell guard compares the missing value as empty, not true.
            return False
        if len(matches) != 1 or not isinstance(matches[0].get("value"), str):
            raise Stop("deployment variable value is unknown")
        return matches[0]["value"] == "true"

    @staticmethod
    def _local_workflow_tree(value: str) -> dict[str, str] | None:
        result: dict[str, str] = {}
        for line in value.splitlines():
            metadata, separator, path = line.partition("\t")
            parts = metadata.split()
            if not separator or len(parts) != 3 or parts[1] != "blob":
                return None
            result[path] = parts[2]
        return result

    @staticmethod
    def _workflow_bytes(response: Any) -> bytes:
        if not isinstance(response, dict) or response.get("encoding") != "base64":
            raise Stop("candidate deploy workflow is unavailable")
        content = response.get("content")
        if not isinstance(content, str):
            raise Stop("candidate deploy workflow is unavailable")
        try:
            return base64.b64decode(content, validate=False)
        except ValueError as error:
            raise Stop("candidate deploy workflow is unavailable") from error

    @staticmethod
    def _has_known_deploy_guard(workflow: bytes) -> bool:
        """このrepoの既知workflowでfalse時にAzure手前で止まるguardだけを確認する。"""
        try:
            text = workflow.decode("utf-8")
        except UnicodeDecodeError:
            return False
        required = (
            "if: github.ref == 'refs/heads/main'",
            "DEPLOY_ENABLED: ${{ vars.AZURE_DEPLOY_ENABLED }}",
            'test "${DEPLOY_ENABLED}" = "true"',
            "- name: Sign in to Azure with OIDC",
        )
        return all(fragment in text for fragment in required)

    def inspect(self, approval: Approval) -> Runtime:
        inherited = tuple(
            name for name in ("GH_TOKEN", "GITHUB_TOKEN") if self.environment.get(name)
        )
        remote = self._run("git", "ls-remote", "origin", "refs/heads/main").split()
        if len(remote) != 2 or remote[1] != "refs/heads/main":
            raise Stop("remote main SHA is unavailable")
        trusted_workflow = self._run_bytes(
            "git", "show", f"{approval.trusted_source}:.github/workflows/deploy-azure.yml"
        )
        trusted_tree = self._local_workflow_tree(
            self._run("git", "ls-tree", "-r", approval.trusted_source, "--", ".github/workflows")
        )
        candidate_response = self._api_json(
            f"repos/{REPOSITORY}/contents/.github/workflows/deploy-azure.yml?ref={approval.expected_head}"
        )
        candidate_workflow = self._workflow_bytes(candidate_response)
        candidate_tree = self._workflow_tree(
            self._api_json(f"repos/{REPOSITORY}/git/trees/{approval.expected_head}?recursive=1")
        )
        environment = self._api_json(f"repos/{REPOSITORY}/environments/azure-production")
        protection_rules = (
            environment.get("protection_rules") if isinstance(environment, dict) else None
        )
        deploy_enabled = self._deployment_enabled()
        return Runtime(
            repository=REPOSITORY,
            remote_main=remote[0],
            checkout_head=self._run("git", "rev-parse", "HEAD"),
            checkout_tree=self._run("git", "rev-parse", "HEAD^{tree}"),
            detached=not self._run("git", "branch", "--show-current"),
            clean=not self._run("git", "status", "--porcelain"),
            actor=self._run("gh", "api", "user", "--jq", ".login"),
            inherited_tokens=inherited,
            candidate_workflow_tree_matches_trusted=(
                trusted_tree is not None and candidate_tree == trusted_tree
            ),
            trusted_deploy_blob_is_audited=(
                trusted_tree is not None
                and trusted_tree.get(".github/workflows/deploy-azure.yml") == AUDITED_DEPLOY_BLOB
            ),
            candidate_deploy_blob_is_audited=(
                isinstance(candidate_response, dict)
                and candidate_response.get("sha") == AUDITED_DEPLOY_BLOB
            ),
            candidate_deploy_matches_trusted=candidate_workflow == trusted_workflow,
            candidate_has_known_deploy_guard=self._has_known_deploy_guard(candidate_workflow),
            deploy_enabled=deploy_enabled,
            environment_approval_required=(
                len(protection_rules) > 0 if isinstance(protection_rules, list) else None
            ),
        )

    def verify_evidence(self, approval: Approval) -> None:
        review_pr, review_id = _review_comment(approval.review_url)
        if review_pr != approval.pr_number:
            raise Stop("review evidence targets another pull request")
        review = self._api_json(f"repos/{REPOSITORY}/issues/comments/{review_id}")
        ci_run = self._api_json(f"repos/{REPOSITORY}/actions/runs/{_run_id(approval.ci_url)}")
        authority_id = _comment_id(approval.authority_receipt, "authority_receipt")
        authority = self._api_json(f"repos/{REPOSITORY}/issues/comments/{authority_id}")
        review_body = review.get("body") if isinstance(review, dict) else None
        if not isinstance(review_body, str) or (
            REVIEW_MARKER not in review_body
            or f"head: {approval.expected_head}" not in review_body
            or "verdict: pass" not in review_body
        ):
            raise Stop("independent review evidence does not bind the expected head")
        pull_requests = ci_run.get("pull_requests") if isinstance(ci_run, dict) else None
        if not isinstance(ci_run, dict) or (
            ci_run.get("head_sha") != approval.expected_head
            or ci_run.get("conclusion") != "success"
            or ci_run.get("event") != "pull_request"
            or not isinstance(pull_requests, list)
            or len(pull_requests) != 1
            or not isinstance(pull_requests[0], dict)
            or pull_requests[0].get("number") != approval.pr_number
        ):
            raise Stop("CI evidence does not bind the expected head")
        authority_body = authority.get("body") if isinstance(authority, dict) else None
        authority_login = (
            authority.get("user", {}).get("login") if isinstance(authority, dict) else None
        )
        required = (
            APPROVAL_MARKER,
            f"packet: {approval.packet_id}",
            f"trusted_source: {approval.trusted_source}",
            f"expected_head: {approval.expected_head}",
            f"pr_number: {approval.pr_number}",
            f"execution_mode: {approval.execution_mode}",
            f"expires_at: {approval.expires_at.isoformat()}",
        )
        if (
            not isinstance(authority_body, str)
            or authority_login != approval.expected_login
            or any(item not in authority_body for item in required)
        ):
            raise Stop("authority evidence does not bind this approval packet")

    def stored_token(self) -> str:
        return self._run("gh", "auth", "token")


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise Stop(f"{field} must be a lowercase 40-character SHA")
    return value


def _expiry(value: Any) -> datetime:
    if not isinstance(value, str):
        raise Stop("expires_at is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Stop("expires_at is invalid") from error
    if result.tzinfo is None or result <= datetime.now(UTC):
        raise Stop("approval packet is expired")
    return result


def ensure_not_expired(approval: Approval) -> None:
    if approval.expires_at <= datetime.now(UTC):
        raise Stop("approval packet is expired")


def receipt_path_for(approval: Approval) -> Path:
    """同じauthorityに別packet_id/pathを与えてもreceiptを回避できない固定key。"""
    key = json.dumps(
        {
            "authority_receipt": approval.authority_receipt,
            "execution_mode": approval.execution_mode,
            "expected_head": approval.expected_head,
            "pr_number": approval.pr_number,
            "repository": REPOSITORY,
            "trusted_source": approval.trusted_source,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return RECEIPT_ROOT / f"{hashlib.sha256(key).hexdigest()}.json"


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
        "expires_at",
        "allow_merge",
        "allow_post_dispatch",
        "execution_mode",
    }
    if set(raw) != expected:
        raise Stop("approval packet fields are incomplete or unexpected")
    if raw["repository"] != REPOSITORY or raw["allow_merge"] is not True:
        raise Stop("repository or merge approval is invalid")
    if raw["allow_post_dispatch"] is not False:
        raise Stop("post-merge dispatch must be prohibited")
    if raw["execution_mode"] not in {"normal", "bootstrap"}:
        raise Stop("execution mode is invalid")
    if not isinstance(raw["pr_number"], int) or raw["pr_number"] < 1:
        raise Stop("pr_number is invalid")
    for field in ("packet_id", "expected_login"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise Stop(f"{field} is required")
    _comment_id(raw["authority_receipt"], "authority_receipt")
    _review_comment(raw["review_url"])
    _run_id(raw["ci_url"])
    return Approval(
        raw["packet_id"],
        raw["pr_number"],
        _sha(raw["expected_head"], "expected_head"),
        _sha(raw["trusted_source"], "trusted_source"),
        _sha(raw["source_tree"], "source_tree"),
        raw["expected_login"],
        raw["authority_receipt"],
        raw["review_url"],
        raw["ci_url"],
        _expiry(raw["expires_at"]),
        raw["execution_mode"],
    )


def validate_runtime(approval: Approval, runtime: Runtime) -> None:
    """Git/gh/remote/environmentの実読取結果がpacketと一致しなければ停止する。"""

    if runtime.repository != REPOSITORY or runtime.inherited_tokens:
        raise Stop("repository or inherited token is invalid")
    if runtime.actor != approval.expected_login or not runtime.clean or not runtime.detached:
        raise Stop("stored gh login or clean detached checkout is invalid")
    if (
        runtime.remote_main != approval.trusted_source
        or runtime.checkout_head != approval.trusted_source
    ):
        raise Stop("remote main or checkout source mismatch")
    if runtime.checkout_tree != approval.source_tree:
        raise Stop("trusted tree mismatch")
    if runtime.environment_approval_required is None:
        raise Stop("environment protection state is unknown")
    if (
        runtime.candidate_workflow_tree_matches_trusted is not True
        or runtime.trusted_deploy_blob_is_audited is not True
        or runtime.candidate_deploy_blob_is_audited is not True
        or runtime.candidate_deploy_matches_trusted is not True
        or runtime.candidate_has_known_deploy_guard is not True
        or runtime.deploy_enabled is not False
    ):
        raise Stop("candidate deployment behavior cannot be proven non-executing")


def execute_with_runner(
    approval: Approval,
    adapter: Adapter,
    runner: Callable[[str, Approval], str],
    receipt_store: ReceiptStore,
) -> OperationReceipt:
    """承認後に固定sourceの既存 strict gateへ繋ぐ一点。testsはfake runnerだけを渡す。"""

    runtime = adapter.inspect(approval)
    validate_runtime(approval, runtime)
    adapter.verify_evidence(approval)
    receipt = receipt_store.reserve(approval)
    token = adapter.stored_token()
    if not token:
        raise Stop("stored gh token is unavailable")
    try:
        result = runner(token, approval)
    except Exception as error:
        receipt_store.finish(receipt, "unknown")
        raise Stop("strict gate result is unknown; retry is forbidden") from error
    return receipt_store.finish(receipt, result)


def _trusted_gate_module(trusted_source: str) -> Any:
    """固定git objectから既存strict gateを読込み、local entryから後続dispatchを禁じる。"""

    safe_environment = dict(os.environ)
    safe_environment.pop("GH_TOKEN", None)
    safe_environment.pop("GITHUB_TOKEN", None)
    try:
        source = subprocess.run(
            ("git", "show", f"{trusted_source}:scripts/merge_gate.py"),
            check=True,
            capture_output=True,
            env=safe_environment,
        ).stdout.decode("utf-8")
    except (OSError, UnicodeDecodeError, subprocess.CalledProcessError) as error:
        raise Stop("trusted strict gate source is unavailable") from error
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    with tempfile.TemporaryDirectory(prefix="agent-world-trusted-gate-") as directory:
        module_path = Path(directory) / f"merge_gate_{digest}.py"
        module_path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(f"trusted_merge_gate_{digest}", module_path)
        if spec is None or spec.loader is None:
            raise Stop("trusted strict gate source is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


def _expiry_guarded_put(approval: Approval, original_put: Callable[[str, dict[str, Any]], Any]):
    def guarded_put(path: str, payload: dict[str, Any]) -> Any:
        if path == f"/repos/{REPOSITORY}/pulls/{approval.pr_number}/merge":
            ensure_not_expired(approval)
        return original_put(path, payload)

    return guarded_put


def run_existing_strict_gate(token: str, approval: Approval) -> str:
    """fixed sourceのstrict gateにPUT直前expiry hookを追加する。"""
    module = _trusted_gate_module(approval.trusted_source)
    client = module.GitHubClient(REPOSITORY, token)
    original_put = client.put

    client.put = _expiry_guarded_put(approval, original_put)
    module.execute(
        client,
        module.GateTarget(approval.pr_number, approval.expected_head),
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
            approval,
            SystemAdapter(),
            run_existing_strict_gate,
            ReceiptStore(receipt_path_for(approval)),
        )
        print(json.dumps(asdict(receipt), sort_keys=True))
        return 0
    print(f"approval packet {approval.packet_id} is valid; merge_result=not_run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
