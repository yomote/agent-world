#!/usr/bin/env python3
"""固定したPR headを検査し、明示指定されたときだけsquash mergeする。"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

REVIEW_MARKER = "<!-- agent-world-independent-review -->"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
EXCLUDED_LABELS = {"needs-human", "release"}
EXPECTED_CHECK = "check"
EXPECTED_WORKFLOW = "ci.yml"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REVIEW_RE = re.compile(
    rf"\A{re.escape(REVIEW_MARKER)}\r?\n"
    r"head: ([0-9a-f]{40})\r?\n"
    r"verdict: (pass|fail)\r?\n"
    r"reviewer: ([^\r\n]{1,100})(?:\r?\n|\Z)"
)


class GateError(RuntimeError):
    """判定不能または不合格。どちらもmergeを止める。"""


class GitHubError(GateError):
    pass


class GitHubUnknownError(GitHubError):
    pass


class GitHubClient:
    def __init__(self, repository: str, token: str):
        if repository.count("/") != 1:
            raise GateError("GITHUB_REPOSITORY must be OWNER/REPOSITORY")
        self.repository = repository
        self.owner, self.name = repository.split("/", 1)
        self.token = token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"https://api.github.com{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "agent-world-merge-gate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status == 204:
                    return None
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise GitHubError(f"GitHub API {method} {path}: HTTP {error.code}: {detail}") from error
        except (OSError, ValueError) as error:
            # 書き込み結果が不明な場合も再送しない。
            raise GitHubUnknownError(
                f"GitHub API {method} {path}: result unknown: {error}"
            ) from error

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("POST", path, body)

    def put(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PUT", path, body)

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        result = self.post("/graphql", {"query": query, "variables": variables})
        if not isinstance(result, dict):
            raise GitHubError("GitHub GraphQL response is invalid")
        if result.get("errors"):
            raise GitHubError(f"GitHub GraphQL returned errors: {result['errors']}")
        if not isinstance(result.get("data"), dict):
            raise GitHubError("GitHub GraphQL response has no data")
        return result["data"]


@dataclass(frozen=True)
class GateTarget:
    number: int
    expected_head: str


def _repo_path(client: GitHubClient, suffix: str) -> str:
    return f"/repos/{client.repository}{suffix}"


def validate_pr(pr: dict[str, Any], target: GateTarget) -> None:
    if not isinstance(pr, dict):
        raise GateError("PR response is invalid")
    if pr.get("number") != target.number:
        raise GateError("PR number mismatch")
    if pr.get("state") != "open" or pr.get("draft") is not False:
        raise GateError("PR must be open and Ready for review")
    if pr.get("base", {}).get("ref") != "main":
        raise GateError("PR base must be main")
    if pr.get("head", {}).get("sha") != target.expected_head:
        raise GateError("PR head changed from expected_head")
    if pr.get("mergeable") is not True:
        raise GateError("PR mergeability is false or unknown")
    head_repo = pr.get("head", {}).get("repo", {}).get("full_name")
    base_repo = pr.get("base", {}).get("repo", {}).get("full_name")
    if head_repo != base_repo:
        raise GateError("fork PR is outside the merge gate scope")
    labels = {label.get("name", "").lower() for label in pr.get("labels", [])}
    blocked = labels & EXCLUDED_LABELS
    if blocked:
        raise GateError(f"excluded label present: {', '.join(sorted(blocked))}")


def validate_review(comments: list[dict[str, Any]], expected_head: str) -> str:
    for comment in reversed(comments):
        if not isinstance(comment, dict):
            raise GateError("review comment response is invalid")
        match = REVIEW_RE.match(comment.get("body") or "")
        if not match or match.group(1) != expected_head:
            continue
        if comment.get("author_association") not in TRUSTED_ASSOCIATIONS:
            continue
        if match.group(2) == "fail":
            raise GateError("latest trusted independent-review declaration rejects current head")
        return match.group(3)
    raise GateError("trusted independent-review evidence for current head is missing")


def validate_ruleset(rulesets: list[dict[str, Any]]) -> None:
    for ruleset in rulesets:
        if not isinstance(ruleset, dict):
            continue
        if ruleset.get("enforcement") != "active" or ruleset.get("target") != "branch":
            continue
        # 欠落は「bypassなし」ではなく権限不足・未知の応答として扱う。
        if ruleset.get("bypass_actors") != []:
            continue
        conditions = ruleset.get("conditions")
        ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
        includes = ref_name.get("include") if isinstance(ref_name, dict) else None
        excludes = ref_name.get("exclude") if isinstance(ref_name, dict) else None
        if not isinstance(includes, list) or not isinstance(excludes, list):
            continue
        if not any(
            pattern == "~DEFAULT_BRANCH"
            or (isinstance(pattern, str) and fnmatch.fnmatchcase("refs/heads/main", pattern))
            for pattern in includes
        ):
            continue
        if any(
            pattern == "~DEFAULT_BRANCH"
            or (isinstance(pattern, str) and fnmatch.fnmatchcase("refs/heads/main", pattern))
            for pattern in excludes
        ):
            continue
        rule_items = ruleset.get("rules")
        if not isinstance(rule_items, list) or not all(
            isinstance(rule, dict) for rule in rule_items
        ):
            continue
        rules = {rule.get("type"): rule.get("parameters", {}) for rule in rule_items}
        pull_request = rules.get("pull_request")
        checks = rules.get("required_status_checks")
        if not isinstance(pull_request, dict) or not isinstance(checks, dict):
            continue
        if not {"deletion", "non_fast_forward", "required_linear_history"} <= rules.keys():
            continue
        contexts = {item.get("context") for item in checks.get("required_status_checks", [])}
        if (
            pull_request.get("required_review_thread_resolution") is True
            and checks.get("strict_required_status_checks_policy") is True
            and EXPECTED_CHECK in contexts
        ):
            return
    raise GateError(
        "an active, bypass-free main ruleset with strict check/thread protection was not found"
    )


def validate_ci(runs: list[dict[str, Any]], target: GateTarget) -> bool:
    matching = [
        run
        for run in runs
        if run.get("head_sha") == target.expected_head
        and run.get("event") == "pull_request"
        and any(item.get("number") == target.number for item in run.get("pull_requests", []))
    ]
    if not matching:
        return False
    if any(
        not all(isinstance(run.get(key), int) for key in ("run_number", "run_attempt", "id"))
        for run in matching
    ):
        raise GateError("current-head CI identity is incomplete")
    latest = max(
        matching,
        key=lambda run: (
            run.get("run_number", -1),
            run.get("run_attempt", -1),
            run.get("id", -1),
        ),
    )
    if latest.get("status") == "completed" and latest.get("conclusion") == "success":
        return True
    if latest.get("status") != "completed":
        return False
    raise GateError(f"latest current-head CI is not successful: {latest.get('conclusion')}")


def fetch_rulesets(client: GitHubClient) -> list[dict[str, Any]]:
    summaries = client.get(_repo_path(client, "/rulesets?per_page=100"))
    if not isinstance(summaries, list) or len(summaries) == 100:
        raise GateError("rulesets are unavailable or exceed the bounded query")
    if not all(isinstance(item, dict) and isinstance(item.get("id"), int) for item in summaries):
        raise GateError("ruleset identities are incomplete")
    return [client.get(_repo_path(client, f"/rulesets/{item['id']}")) for item in summaries]


def unresolved_threads(client: GitHubClient, number: int) -> int:
    query = """
      query($owner:String!, $name:String!, $number:Int!) {
        repository(owner:$owner, name:$name) {
          pullRequest(number:$number) {
            reviewThreads(first:100) {
              nodes { isResolved }
              pageInfo { hasNextPage }
            }
          }
        }
      }
    """
    data = client.graphql(query, {"owner": client.owner, "name": client.name, "number": number})
    repository = data.get("repository")
    pull_request = repository.get("pullRequest") if isinstance(repository, dict) else None
    threads = pull_request.get("reviewThreads") if isinstance(pull_request, dict) else None
    nodes = threads.get("nodes") if isinstance(threads, dict) else None
    page_info = threads.get("pageInfo") if isinstance(threads, dict) else None
    if (
        not isinstance(nodes, list)
        or not isinstance(page_info, dict)
        or page_info.get("hasNextPage") is not False
    ):
        raise GateError("review threads are missing or exceed the bounded query")
    return sum(not isinstance(node, dict) or node.get("isResolved") is not True for node in nodes)


def fetch_runs(client: GitHubClient, target: GateTarget) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {"event": "pull_request", "head_sha": target.expected_head, "per_page": 100}
    )
    result = client.get(_repo_path(client, f"/actions/workflows/{EXPECTED_WORKFLOW}/runs?{query}"))
    if not isinstance(result, dict):
        raise GateError("CI workflow runs response is invalid")
    runs = result.get("workflow_runs")
    total_count = result.get("total_count")
    if not isinstance(runs, list) or not isinstance(total_count, int) or total_count > len(runs):
        raise GateError("CI workflow runs are unavailable or exceed the bounded query")
    return runs


def evaluate(client: GitHubClient, target: GateTarget, *, require_ci: bool = True) -> str:
    repo = client.get(_repo_path(client, ""))
    if not isinstance(repo, dict):
        raise GateError("repository response is invalid")
    if repo.get("default_branch") != "main" or repo.get("allow_auto_merge") is not False:
        raise GateError("repository main/auto-merge-disabled settings are not applied")
    pr = client.get(_repo_path(client, f"/pulls/{target.number}"))
    validate_pr(pr, target)
    comments = client.get(_repo_path(client, f"/issues/{target.number}/comments?per_page=100"))
    if not isinstance(comments, list) or len(comments) == 100:
        raise GateError("review comments are unavailable or exceed the bounded query")
    reviewer = validate_review(comments, target.expected_head)
    if unresolved_threads(client, target.number):
        raise GateError("unresolved review threads remain")
    validate_ruleset(fetch_rulesets(client))
    if require_ci and not validate_ci(fetch_runs(client, target), target):
        raise GateError("current-head CI is still running")
    return reviewer


def wait_for_ci(client: GitHubClient, target: GateTarget, attempts: int, interval: int) -> None:
    for attempt in range(attempts):
        if validate_ci(fetch_runs(client, target), target):
            return
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise GateError(f"current-head CI did not succeed after {attempts} bounded checks")


def verify_bootstrap_source(expected_head: str) -> None:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], check=True, capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise GateError(f"cannot verify bootstrap source: {error}") from error
    if head != expected_head or dirty:
        raise GateError("bootstrap requires clean checkout at expected_head")


def dispatch_post_merge(client: GitHubClient, target: GateTarget, merge_sha: str) -> None:
    client.post(
        _repo_path(client, f"/actions/workflows/{EXPECTED_WORKFLOW}/dispatches"),
        {"ref": "main"},
    )
    client.post(
        _repo_path(client, "/dispatches"),
        {
            "event_type": "agent-world-merged",
            "client_payload": {
                "pr_number": target.number,
                "head_sha": target.expected_head,
                "merge_commit_sha": merge_sha,
            },
        },
    )


def execute(
    client: GitHubClient,
    target: GateTarget,
    *,
    attempts: int,
    interval: int,
    dispatch_after_merge: bool = False,
) -> str:
    # CI以外を先に検査し、失敗したPRのために待機しない。
    reviewer = evaluate(client, target, require_ci=False)
    wait_for_ci(client, target, attempts, interval)
    # 待機中のコメント削除、thread追加、ruleset変更もmerge直前に検出する。
    reviewer = evaluate(client, target)
    try:
        result = client.put(
            _repo_path(client, f"/pulls/{target.number}/merge"),
            {"sha": target.expected_head, "merge_method": "squash"},
        )
    except GitHubUnknownError as error:
        # 再送せずread-only確認を一度だけ行い、確認できなければunknownのまま止める。
        pr = client.get(_repo_path(client, f"/pulls/{target.number}"))
        merge_sha = pr.get("merge_commit_sha")
        if (
            pr.get("merged") is True
            and pr.get("head", {}).get("sha") == target.expected_head
            and isinstance(merge_sha, str)
            and FULL_SHA_RE.fullmatch(merge_sha)
        ):
            result = {"merged": True, "sha": merge_sha}
        else:
            raise GateError("merge write result remains unknown after one read") from error
    if not isinstance(result, dict):
        raise GateError("merge response is invalid")
    if result.get("merged") is not True:
        raise GateError(f"GitHub did not merge the PR: {result.get('message', 'unknown')}")
    merge_sha = result.get("sha")
    if not isinstance(merge_sha, str) or not FULL_SHA_RE.fullmatch(merge_sha):
        raise GateError("merge succeeded but merge commit SHA is unknown")
    if dispatch_after_merge:
        # GITHUB_TOKENのpushはworkflowを起動しないため、後続を明示dispatchする。
        dispatch_post_merge(client, target, merge_sha)
    return reviewer


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("pr_number", type=int)
    parser.add_argument("expected_head")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dispatch-after-merge", action="store_true")
    parser.add_argument("--bootstrap-source", action="store_true")
    parser.add_argument("--ci-attempts", type=int, default=10)
    parser.add_argument("--ci-interval", type=int, default=60)
    args = parser.parse_args(argv)
    if args.pr_number < 1 or not FULL_SHA_RE.fullmatch(args.expected_head):
        parser.error("positive PR number and lowercase 40-character SHA are required")
    if not 1 <= args.ci_attempts <= 10 or args.ci_interval < 60:
        parser.error("CI budget is at most 10 checks, at intervals of at least 60 seconds")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN is required", file=sys.stderr)
        return 2
    try:
        client = GitHubClient(repository, token)
        if args.execute:
            if args.bootstrap_source:
                verify_bootstrap_source(args.expected_head)
            reviewer = execute(
                client,
                GateTarget(args.pr_number, args.expected_head),
                attempts=args.ci_attempts,
                interval=args.ci_interval,
                dispatch_after_merge=args.dispatch_after_merge,
            )
            print(f"merged PR #{args.pr_number} at {args.expected_head} after review by {reviewer}")
        else:
            reviewer = evaluate(client, GateTarget(args.pr_number, args.expected_head))
            print(f"eligible PR #{args.pr_number} at {args.expected_head}; reviewer={reviewer}")
        return 0
    except GateError as error:
        print(f"merge gate blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
