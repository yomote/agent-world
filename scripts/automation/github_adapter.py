"""既存GCM認証をprocess内だけで使う唯一のGitHub REST/GraphQL adapter。"""

import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

from .transport import Stop

REPOSITORY = "yomote/agent-world"
API = "https://api.github.com"
PREFIX = f"/repos/{REPOSITORY}"
LIMIT = 30
MAX_BYTES = 4 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}\Z")
OPERATIONS = {
    "create_draft_pr",
    "post_review_evidence",
    "ready_pr",
    "current_ci",
    "current_ci_jobs",
    "main_protection",
    "merge_snapshot",
    "normal_merge",
    "create_helper_issue",
    "close_issue",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def existing_gcm(workspace):
    """認証応答を返却・記録せず内部headerだけに使用。対話・新規loginは禁止。"""
    helpers = subprocess.run(
        ["git", "config", "--get-all", "credential.helper"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.splitlines()
    if helpers != ["manager"]:
        raise Stop("approval_wait", "existing_gcm_helper_not_confirmed")
    result = subprocess.run(
        [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "credential.helper=manager",
            "-c",
            "credential.interactive=never",
            "credential",
            "fill",
        ],
        input="protocol=https\nhost=github.com\npath=yomote/agent-world.git\n\n",
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "GCM_INTERACTIVE": "Never", "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        raise Stop("approval_wait", "existing_gcm_auth_unavailable")
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if fields.get("host") != "github.com" or not fields.get("password"):
        raise Stop("approval_wait", "existing_gcm_auth_unavailable")
    return "Bearer " + fields["password"]


class GitHub:
    def __init__(self, task):
        self.task = task
        self.authorization = None
        self.opener = urllib.request.build_opener(NoRedirect())
        task.db.executescript("""
            CREATE TABLE IF NOT EXISTS delivery_http_budget(id INTEGER PRIMARY KEY, used INTEGER);
            INSERT OR IGNORE INTO delivery_http_budget VALUES(1,0);
            CREATE TABLE IF NOT EXISTS delivery_http_requests(
                seq INTEGER PRIMARY KEY,campaign TEXT,operation TEXT,method TEXT,path TEXT,
                state TEXT,status INTEGER,at REAL);
            CREATE TABLE IF NOT EXISTS delivery_http_cache(
                path TEXT PRIMARY KEY,etag TEXT,data TEXT);
            CREATE TABLE IF NOT EXISTS delivery_merge_attempts(
                campaign TEXT,head TEXT,PRIMARY KEY(campaign,head));
        """)

    def git_transfer(self, workspace, operation, *, head=None, branch=None):
        """Git object輸送もdeliveryだけが実行する。REST/GraphQLとは別のGit protocol。"""
        self.task.boundary()
        if (
            operation == "push"
            and SHA.fullmatch(head or "")
            and re.fullmatch(r"codex/[a-z0-9-]+", branch or "")
        ):
            arguments = ["push", "origin", f"{head}:refs/heads/{branch}"]
        elif operation == "fetch":
            arguments = ["fetch", "origin", "main"]
        else:
            raise Stop("stopped", "git_transfer_not_allowed")
        self.task.save_event(self.task.data(), "git_" + operation + "_reserved")
        try:
            result = subprocess.run(
                ["git", *arguments],
                cwd=workspace,
                capture_output=True,
                timeout=30,
                env={**os.environ, "GCM_INTERACTIVE": "Never", "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode:
                raise Stop("unknown", "git_" + operation + "_unconfirmed")
        except (OSError, subprocess.TimeoutExpired):
            raise Stop("unknown", "git_" + operation + "_unconfirmed") from None
        self.task.save_event(self.task.data(), "git_" + operation + "_completed")

    def request(self, operation, method, path, payload=None):
        prefix = re.escape(PREFIX)
        routes = {
            "create_draft_pr": ("POST", prefix + r"/pulls"),
            "post_review_evidence": ("POST", prefix + r"/issues/[1-9][0-9]*/comments"),
            "ready_pr": ("POST", r"/graphql"),
            "merge_snapshot": ("POST", r"/graphql"),
            "current_ci": (
                "GET",
                prefix
                + r"/actions/workflows/ci\.yml/runs\?event=pull_request"
                + r"&head_sha=[0-9a-f]{40}&per_page=100",
            ),
            "current_ci_jobs": ("GET", prefix + r"/actions/runs/[1-9][0-9]*/jobs\?per_page=100"),
            "main_protection": ("GET", prefix + r"/branches/main"),
            "normal_merge": ("PUT", prefix + r"/pulls/[1-9][0-9]*/merge"),
            "create_helper_issue": ("POST", prefix + r"/issues"),
            "close_issue": ("PATCH", prefix + r"/issues/[1-9][0-9]*"),
        }
        route = routes.get(operation)
        if not route or method != route[0] or not re.fullmatch(route[1], path):
            raise Stop("stopped", "http_target_not_allowed")
        # operationごとの固定path生成以外から呼べない。redirect/retry/page followは行わない。
        if "/dispatches" in path or ".." in path or "#" in path or "\\" in path:
            raise Stop("stopped", "http_target_not_allowed")
        task = self.task
        task.boundary()
        with task.db:
            used = task.db.execute("SELECT used FROM delivery_http_budget WHERE id=1").fetchone()[0]
            if used >= LIMIT:
                raise Stop("stopped", "http_request_budget_30")
            task.db.execute("UPDATE delivery_http_budget SET used=used+1 WHERE id=1")
            cursor = task.db.execute(
                "INSERT INTO delivery_http_requests(campaign,operation,method,path,state,at) "
                "VALUES (?,?,?,?,'reserved',?)",
                (task.name, operation, method, path, time.time()),
            )
            sequence = cursor.lastrowid
        task.save_event(task.data(), "http_request_reserved")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-world-bounded-local",
            "Content-Type": "application/json",
        }
        if self.authorization is None:
            self.authorization = existing_gcm(task.root)
        headers["Authorization"] = self.authorization
        cached = task.db.execute(
            "SELECT etag,data FROM delivery_http_cache WHERE path=?", (path,)
        ).fetchone()
        if method == "GET" and cached:
            headers["If-None-Match"] = cached[0]
        request = urllib.request.Request(
            API + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers,
            method=method,
        )
        status = None
        try:
            try:
                response = self.opener.open(
                    request, timeout=min(15, max(0.1, task.data()["deadline"] - time.time()))
                )
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.status
                if status == 304 and cached and method == "GET":
                    data = json.loads(cached[1])
                elif not 200 <= status < 300:
                    retry_after = response.headers.get("Retry-After")
                    reset = response.headers.get("X-RateLimit-Reset")
                    snapshot = task.data()
                    snapshot["last_http_status"] = status
                    snapshot["rate_limit"] = {"retry_after": retry_after, "reset": reset}
                    task.save_event(snapshot, "http_failure_stopped")
                    state = (
                        "approval_wait"
                        if status in (401, 403)
                        else "unknown"
                        if method != "GET"
                        else "stopped"
                    )
                    raise Stop(state, "github_http_" + str(status))
                else:
                    body = response.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES or 'rel="next"' in response.headers.get("Link", ""):
                        raise Stop(
                            "unknown" if method != "GET" else "stopped", "http_response_incomplete"
                        )
                    data = json.loads(body)
                    if path == "/graphql" and data.get("errors"):
                        raise Stop("unknown", "graphql_errors_no_retry")
                    etag = response.headers.get("ETag")
                    if method == "GET" and etag:
                        with task.db:
                            task.db.execute(
                                "INSERT OR REPLACE INTO delivery_http_cache VALUES (?,?,?)",
                                (path, etag, json.dumps(data)),
                            )
            with task.db:
                task.db.execute(
                    "UPDATE delivery_http_requests SET state='received',status=? WHERE seq=?",
                    (status, sequence),
                )
            return data
        except (OSError, ValueError, Stop) as error:
            with task.db:
                task.db.execute(
                    "UPDATE delivery_http_requests SET state='stopped',status=? WHERE seq=?",
                    (status, sequence),
                )
            if isinstance(error, Stop):
                raise
            raise Stop(
                "unknown" if method != "GET" else "stopped", "github_transport_no_retry"
            ) from None

    def call(self, operation, args):
        if operation not in OPERATIONS:
            raise Stop("stopped", "github_operation_not_allowed")
        repository = args.get("repository_full_name", args.get("repo_full_name", REPOSITORY))
        if repository != REPOSITORY:
            raise Stop("stopped", "github_repository_not_allowed")
        number = args.get("pr_number", args.get("issue_number"))
        if number is not None and (type(number) is not int or number < 1):
            raise Stop("stopped", "github_number_invalid")
        if operation == "create_draft_pr":
            if args["base_branch"] != "main" or not args["head_branch"].startswith("codex/"):
                raise Stop("stopped", "github_branch_not_allowed")
            result = self.request(
                operation,
                "POST",
                PREFIX + "/pulls",
                {
                    "head": args["head_branch"],
                    "base": "main",
                    "title": args["title"],
                    "body": args["body"],
                    "draft": True,
                },
            )
            data = self.task.data()
            data["pr_node_id"] = result["node_id"]
            self.task.save_event(data, "draft_identity_received")
            return result
        if operation == "post_review_evidence":
            return self.request(
                operation, "POST", f"{PREFIX}/issues/{number}/comments", {"body": args["comment"]}
            )
        if operation == "ready_pr":
            node = self.task.data().get("pr_node_id")
            if not node:
                raise Stop("stopped", "pr_node_id_missing")
            result = self.request(
                operation,
                "POST",
                "/graphql",
                {
                    "query": "mutation($id:ID!){markPullRequestReadyForReview("
                    "input:{pullRequestId:$id}){pullRequest{number isDraft}}}",
                    "variables": {"id": node},
                },
            )
            pr = result["data"]["markPullRequestReadyForReview"]["pullRequest"]
            if pr["number"] != number or pr["isDraft"] is not False:
                raise Stop("unknown", "ready_result_unverified")
            return pr
        if operation == "current_ci":
            if not SHA.fullmatch(args["commit_sha"]):
                raise Stop("stopped", "ci_head_invalid")
            return self.request(
                operation,
                "GET",
                PREFIX
                + "/actions/workflows/ci.yml/runs?event=pull_request&head_sha="
                + args["commit_sha"]
                + "&per_page=100",
            )
        if operation == "current_ci_jobs":
            if type(args["run_id"]) is not int or args["run_id"] < 1:
                raise Stop("stopped", "ci_run_invalid")
            return self.request(
                operation, "GET", f"{PREFIX}/actions/runs/{args['run_id']}/jobs?per_page=100"
            )
        if operation == "main_protection":
            return self.request(operation, "GET", PREFIX + "/branches/main")
        if operation == "merge_snapshot":
            return self.snapshot(number)
        if operation == "normal_merge":
            data = self.task.data()
            head = args["expected_head_sha"]
            check = data.get("current_check", {})
            review = data.get("review", {})
            ci = data.get("ci", {})
            if (
                not SHA.fullmatch(head)
                or data.get("merge_validated_head") != head
                or data.get("pr") != number
                or time.time() - data.get("merge_validated_at", 0) > 60
                or review.get("head") != head
                or review.get("verdict") != "pass"
                or review.get("reviewer") != "01a07c70-6ca8-7fb0-af44-05aefe156087"
                or ci.get("head_sha") != head
                or ci.get("pr_number") != number
                or ci.get("conclusion") != "success"
                or not ci.get("check_job_id")
                or check.get("head") != head
                or check.get("end_head") != head
                or check.get("exit_code") != 0
                or check.get("clean") is not True
            ):
                raise Stop("stopped", "merge_packet_not_bound")
            if data.get("protection", {}).get("protected") is not True:
                raise Stop("stopped", "merge_protection_not_bound")
            from scripts.merge_gate import GateTarget, validate_pr, validate_review

            snapshot = data.get("merge_snapshot", {})
            try:
                validate_pr(snapshot["pr"], GateTarget(number, head))
                if validate_review(snapshot["comments"], head) != review["reviewer"]:
                    raise ValueError()
                if any(node.get("isResolved") is not True for node in snapshot["threads"]):
                    raise ValueError()
            except (RuntimeError, KeyError, ValueError):
                raise Stop("stopped", "merge_snapshot_not_bound") from None
            try:
                with self.task.db:
                    self.task.db.execute(
                        "INSERT INTO delivery_merge_attempts VALUES (?,?)", (self.task.name, head)
                    )
            except sqlite3.IntegrityError:
                raise Stop("unknown", "merge_already_attempted_no_resend") from None
            return self.request(
                operation,
                "PUT",
                f"{PREFIX}/pulls/{number}/merge",
                {"sha": head, "merge_method": "squash"},
            )
        if operation == "create_helper_issue":
            return self.request(
                operation,
                "POST",
                PREFIX + "/issues",
                {"title": args["title"], "body": args["body"]},
            )
        return self.request(
            operation,
            "PATCH",
            f"{PREFIX}/issues/{number}",
            {"state": "closed", "state_reason": "completed"},
        )

    def snapshot(self, number):
        result = self.request(
            "merge_snapshot",
            "POST",
            "/graphql",
            {
                "query": """query($n:Int!){repository(owner:"yomote",name:"agent-world"){
                pullRequest(number:$n){number state isDraft headRefOid baseRefName mergeable
                headRepository{nameWithOwner} baseRepository{nameWithOwner}
                labels(first:100){nodes{name} pageInfo{hasNextPage}}
                comments(first:100){nodes{body authorAssociation} pageInfo{hasNextPage}}
                reviewThreads(first:100){nodes{isResolved} pageInfo{hasNextPage}}}}}""",
                "variables": {"n": number},
            },
        )
        pr = result["data"]["repository"]["pullRequest"]
        if (
            pr["comments"]["pageInfo"]["hasNextPage"]
            or pr["reviewThreads"]["pageInfo"]["hasNextPage"]
            or pr["labels"]["pageInfo"]["hasNextPage"]
        ):
            raise Stop("stopped", "merge_snapshot_incomplete")
        return {
            "pr": {
                "number": pr["number"],
                "state": pr["state"].lower(),
                "draft": pr["isDraft"],
                "head": {
                    "sha": pr["headRefOid"],
                    "repo": {"full_name": pr["headRepository"]["nameWithOwner"]},
                },
                "base": {
                    "ref": pr["baseRefName"],
                    "repo": {"full_name": pr["baseRepository"]["nameWithOwner"]},
                },
                "mergeable": pr["mergeable"] == "MERGEABLE",
                "labels": pr["labels"]["nodes"],
            },
            "comments": [
                {"body": node["body"], "author_association": node["authorAssociation"]}
                for node in pr["comments"]["nodes"]
            ],
            "threads": pr["reviewThreads"]["nodes"],
        }
