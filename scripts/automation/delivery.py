"""明示許可されたrunner/helperのjob→review→CI→通常mergeを駆動する。"""

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from .evidence import thread_id
from .jobs import run_job
from .runner import ROOT, Runner, dispatcher, safe_path
from .transport import Stop, Transport, atomic_json

REPOSITORY = "yomote/agent-world"
REVIEWER = "01a07c70-6ca8-7fb0-af44-05aefe156087"
SHA = re.compile(r"[0-9a-f]{40}\Z")
SCOPES = {
    "runner": (
        "scripts/automation/",
        "scripts/tests/test_self_improvement",
        "docs/runbooks/self-improvement.md",
        "docs/adr/0009-bounded-local-improvement.md",
        "AGENTS.md",
    ),
    "billing-helper": (
        "scripts/automation/billing_debrief.py",
        "scripts/tests/test_billing_debrief.py",
        "docs/runbooks/billing-debrief-helper.md",
    ),
}


def git(workspace: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    ).stdout.strip()


class Campaign:
    def __init__(self, runner: Runner, name: str):
        self.db, self.root, self.name = runner.db, runner.root, name
        if name not in SCOPES:
            raise ValueError("unsupported_campaign")
        self.directory = safe_path(self.root, f"artifacts/self-improvement/{name}")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS delivery_campaigns(
                name TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS delivery_events(
                seq INTEGER PRIMARY KEY,campaign TEXT,at REAL,kind TEXT,data TEXT);
            CREATE TABLE IF NOT EXISTS delivery_operations(
                id TEXT PRIMARY KEY,campaign TEXT,operation TEXT,state TEXT,
                request TEXT,response TEXT);
        """)
        self.token = None
        self.monotonic_deadline = time.monotonic()
        self.transport = Transport(self)

    def data(self):
        row = self.db.execute(
            "SELECT data FROM delivery_campaigns WHERE name=?", (self.name,)
        ).fetchone()
        if row is None:
            raise ValueError("campaign_init_required")
        return json.loads(row[0])

    def save(self, data):
        self.db.execute(
            "INSERT OR REPLACE INTO delivery_campaigns VALUES (?,?)", (self.name, json.dumps(data))
        )

    def save_event(self, data, kind):
        data["updated_at"] = time.time()
        data["last_event"] = kind
        with self.db:
            self.save(data)
            self.db.execute(
                "INSERT INTO delivery_events(campaign,at,kind,data) VALUES (?,?,?,?)",
                (self.name, time.time(), kind, json.dumps(data)),
            )
        atomic_json(self.directory / "status.json", data)

    def init(self, owner: str, base: str, *, seconds=7200, cli_starts=3, connector_calls=40):
        if self.db.execute(
            "SELECT 1 FROM delivery_campaigns WHERE name=?", (self.name,)
        ).fetchone():
            raise ValueError("campaign_exists_no_budget_reset")
        if (
            not SHA.fullmatch(base)
            or not 60 <= seconds <= 7200
            or not 1 <= cli_starts <= 3
            or not 1 <= connector_calls <= 40
        ):
            raise ValueError("invalid_campaign_budget")
        data = dict(
            owner=thread_id(owner),
            name=self.name,
            base=base,
            state="ready",
            reason="initialized",
            deadline=time.time() + seconds,
            last_seen=time.time(),
            token=None,
            lease_until=None,
            cli_starts=0,
            max_cli_starts=cli_starts,
            connector_calls=0,
            max_connector_calls=connector_calls,
            ci_queries={},
            head=None,
            pr=None,
            job_owner=None,
            cost_hardcap="not_provided",
            model_request_hardcap="not_provided",
            upstream_http_hardcap="connector_internal_not_provided",
        )
        self.save_event(data, "initialized")

    def enter(self):
        data = self.data()
        if data["state"] in {
            "approval_wait",
            "unknown",
            "stopped",
            "failed",
            "merged",
            "completed",
        }:
            raise Stop(data["state"], data["reason"])
        if data["token"]:
            if time.time() < data["lease_until"]:
                raise Stop("stopped", "previous_owner_lease_active")
            self.finish_step("unknown", "expired_owner_no_operation_replay")
            raise Stop("unknown", "expired_owner_no_operation_replay")
        self.token = str(uuid4())
        self.monotonic_deadline = time.monotonic() + max(0, data["deadline"] - time.time())
        data["token"] = self.token
        data["lease_until"] = time.time() + 30
        self.save_event(data, "dispatcher_acquired")
        self.boundary()

    def boundary(self):
        data = self.data()
        if data["token"] != self.token:
            raise Stop("unknown", "owner_fence")
        if time.time() < data["last_seen"]:
            raise Stop("stopped", "clock_rollback")
        if time.time() >= data["deadline"] or time.monotonic() >= self.monotonic_deadline:
            raise Stop("stopped", "campaign_time_budget")

    def heartbeat(self):
        self.boundary()
        data = self.data()
        if data["lease_until"] - time.time() < 20:
            data["lease_until"] = min(time.time() + 30, data["deadline"])
            data["last_seen"] = time.time()
            with self.db:
                self.save(data)
            atomic_json(self.directory / "status.json", data)

    def finish_step(self, state, reason):
        data = self.data()
        data.update(state=state, reason=reason, token=None, lease_until=None)
        self.save_event(data, reason)

    def smoke(self):
        self.enter()
        result = run_job(
            self,
            self.root,
            "read-only jobです。編集・tool呼出・外部アクセス・子起動は禁止。"
            "最終回答は LOCAL_RUNNER_JOB_OK の1行のみ。",
            seconds=180,
            read_only=True,
        )
        if result["message"] != "LOCAL_RUNNER_JOB_OK":
            raise Stop("unknown", "smoke_marker_missing")
        self.finish_step("job_verified", "real_cli_job_verified")

    def check_scope(self, workspace, head):
        if git(workspace, "status", "--porcelain") or git(workspace, "rev-parse", "HEAD") != head:
            raise Stop("stopped", "head_dirty_or_moved")
        base = self.data().get("integration_base", self.data()["base"])
        paths = git(workspace, "diff", "--name-only", base, head).splitlines()
        if not paths or any(
            not any(
                path == entry
                or (entry.endswith("/") and path.startswith(entry))
                or (entry.endswith("test_self_improvement") and path.startswith(entry))
                for entry in SCOPES[self.name]
            )
            for path in paths
        ):
            raise Stop("stopped", "scope_violation")

    def deliver(self, workspace: Path):
        if self.data()["state"] != "job_verified":
            raise Stop("stopped", "real_job_required_before_delivery")
        self.enter()
        expected = self.root if self.name == "runner" else self.directory / "checkout"
        if workspace.resolve() != expected.resolve():
            raise Stop("stopped", "workspace_not_allowed")
        if git(workspace, "remote", "get-url", "origin") != f"https://github.com/{REPOSITORY}.git":
            raise Stop("stopped", "origin_not_allowed")
        head = git(workspace, "rev-parse", "HEAD")
        data = self.data()
        data["integration_base"] = git(workspace, "merge-base", "origin/main", head)
        data["head"] = head
        self.save_event(data, "head_fixed")
        self.check_scope(workspace, head)
        review = self.transport.call(
            "independent_review",
            {
                "head": head,
                "base": data["integration_base"],
                "workspace": str(workspace),
                "reviewer": REVIEWER,
                "scope": SCOPES[self.name],
            },
            seconds=1200,
        )
        if (
            review.get("head") != head
            or review.get("reviewer") != REVIEWER
            or review.get("verdict") != "pass"
        ):
            raise Stop("failed", "independent_review_not_pass")
        data = self.data()
        data["review"] = review
        self.save_event(data, "independent_review_pass")
        check = self.transport.call(
            "current_check", {"head": head, "workspace": str(workspace)}, seconds=900
        )
        if (
            check.get("head") != head
            or check.get("end_head") != head
            or check.get("exit_code") != 0
            or check.get("clean") is not True
        ):
            raise Stop("failed", "current_check_not_pass")
        self.save_event(self.data(), "current_check_pass")
        self.check_scope(workspace, head)
        branch = git(workspace, "branch", "--show-current")
        if not branch.startswith("codex/"):
            raise Stop("stopped", "branch_not_allowed")
        # pushは結果不明時に再送しない。既存Git認証を使い、credentialを取得・保存しない。
        self.save_event(self.data(), "push_reserved")
        try:
            git(workspace, "push", "origin", f"{head}:refs/heads/{branch}")
        except subprocess.SubprocessError as error:
            raise Stop("unknown", "push_result_unknown") from error
        self.save_event(self.data(), "push_completed")
        body = (
            "明示起動するbounded改善の実装と証跡。\n\n"
            f"対象head: {head}\n独立review: {REVIEWER} / pass\n"
            "検証: npm run check / clean current head pass\n"
            "承認待ち・結果不明で停止。Azure操作・credential・保護変更なし。\n"
        )
        if data["pr"] is None:
            pr = self.transport.call(
                "create_draft_pr",
                {
                    "repository_full_name": REPOSITORY,
                    "head_branch": branch,
                    "base_branch": "main",
                    "title": "feat: bounded local improvement " + self.name,
                    "body": body,
                    "draft": True,
                },
                write=True,
            )
        else:
            pr = {"number": data["pr"]}
        number = pr.get("number") or pr.get("pr_number")
        if not isinstance(number, int):
            raise Stop("unknown", "draft_pr_identity_missing")
        data = self.data()
        data["pr"] = number
        self.save_event(data, "draft_pr_created")
        self.transport.call(
            "post_review_evidence",
            {
                "repo_full_name": REPOSITORY,
                "pr_number": number,
                "comment": (
                    f"<!-- agent-world-independent-review -->\nhead: {head}\n"
                    f"verdict: pass\nreviewer: {REVIEWER}\n\n{body}"
                ),
            },
            write=True,
        )
        info = self.transport.call(
            "pr_info", {"repository_full_name": REPOSITORY, "pr_number": number}
        )
        if info.get("draft") is True:
            self.transport.call(
                "ready_pr", {"repository_full_name": REPOSITORY, "pr_number": number}, write=True
            )
        self.save_event(self.data(), "ready_pr")
        self.wait_ci(head, number)
        self.normal_merge(head, number)

    def wait_ci(self, head, number):
        from scripts.merge_gate import GateError, GateTarget, validate_ci

        while True:
            self.boundary()
            data = self.data()
            meter = data["ci_queries"].setdefault(head, {"count": 0, "last_at": 0})
            if meter["count"] >= 10:
                raise Stop("stopped", "ci_query_budget")
            while time.time() < meter["last_at"] + 60:
                self.heartbeat()
                time.sleep(0.25)
            meter.update(count=meter["count"] + 1, last_at=time.time())
            self.save_event(data, "ci_query_reserved")
            result = self.transport.call(
                "current_ci", {"repo_full_name": REPOSITORY, "commit_sha": head}
            )
            runs = result.get("workflow_runs", [])
            if (
                not isinstance(runs, list)
                or result.get("total_count") != len(runs)
                or len(runs) >= 100
            ):
                raise Stop("stopped", "ci_page_incomplete")
            matching = [
                run
                for run in runs
                if run.get("head_sha") == head
                and run.get("event") == "pull_request"
                and any(item.get("number") == number for item in run.get("pull_requests", []))
                and str(run.get("path", "")).split("@", 1)[0] == ".github/workflows/ci.yml"
            ]
            try:
                if not validate_ci(matching, GateTarget(number, head)):
                    continue
            except GateError as error:
                raise Stop("failed", "current_ci_not_success") from error
            latest = max(
                matching,
                key=lambda run: (
                    run.get("run_number", 0),
                    run.get("run_attempt", 0),
                    run.get("id", 0),
                ),
            )
            data = self.data()
            data["ci"] = {
                key: latest.get(key)
                for key in ("id", "head_sha", "run_attempt", "html_url", "conclusion")
            }
            self.save_event(data, "current_ci_pass")
            return

    def normal_merge(self, head, number):
        protection = self.transport.call("main_protection", {})
        if protection.get("protected") is not True:
            raise Stop("stopped", "main_protection_unverified")
        pr = self.transport.call(
            "pr_info", {"repository_full_name": REPOSITORY, "pr_number": number}
        )
        # transportは既存connectorの正規化fieldを返す。未知fieldを推測で補完しない。
        observed = pr.get("head", {}).get("sha") or pr.get("head_sha")
        if observed != head or pr.get("draft") is not False or pr.get("state") != "open":
            raise Stop("stopped", "pr_head_or_ready_unverified")
        if pr.get("mergeable") is not True or pr.get("base", {}).get("ref") != "main":
            raise Stop("stopped", "pr_mergeability_unverified")
        comments = self.transport.call("review_comments", {"number": number})
        from scripts.merge_gate import validate_review

        if not isinstance(comments, list) or len(comments) >= 100:
            raise Stop("stopped", "review_comments_unverified")
        try:
            observed_reviewer = validate_review(comments, head)
        except RuntimeError as error:
            raise Stop("stopped", "current_review_unverified") from error
        if observed_reviewer != REVIEWER:
            raise Stop("stopped", "reviewer_mismatch")
        threads = self.transport.call(
            "review_threads", {"repo_full_name": REPOSITORY, "pr_number": number}
        )
        nodes = threads.get("review_threads")
        if (
            not isinstance(nodes, list)
            or len(nodes) >= 100
            or any(node.get("isResolved") is not True for node in nodes)
        ):
            raise Stop("stopped", "review_threads_unverified")
        merged = self.transport.call(
            "normal_merge",
            {
                "repository_full_name": REPOSITORY,
                "pr_number": number,
                "expected_head_sha": head,
                "merge_method": "squash",
            },
            write=True,
        )
        if merged.get("merged") is not True or not SHA.fullmatch(merged.get("sha", "")):
            raise Stop("unknown", "merge_not_confirmed")
        data = self.data()
        data["merge_sha"] = merged["sha"]
        data["protection"] = {
            "protected": True,
            "mode": "normal_connector_no_override",
            "hidden_bypass_configuration": "not_verified",
        }
        self.save_event(data, "normal_merge_confirmed")
        if data.get("issue"):
            self.transport.call(
                "close_issue",
                {
                    "repository_full_name": REPOSITORY,
                    "issue_number": data["issue"],
                    "state": "closed",
                    "state_reason": "completed",
                },
                write=True,
            )
            self.finish_step("completed", "issue_completed_after_normal_merge")
            return
        self.finish_step("merged", "normal_protected_merge_confirmed")

    def revise(self, workspace):
        data = self.data()
        if data["state"] != "failed" or data["reason"] not in {
            "independent_review_not_pass",
            "current_check_not_pass",
            "current_ci_not_success",
        }:
            raise Stop(data["state"], "revision_not_allowed")
        head = git(workspace, "rev-parse", "HEAD")
        if head == data["head"] or git(workspace, "merge-base", data["head"], head) != data["head"]:
            raise Stop("failed", "revision_requires_descendant_head")
        self.check_scope(workspace, head)
        data.update(state="job_verified", reason="revised_head", head=head)
        self.save_event(data, "revised_head_same_budget")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", choices=SCOPES)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--owner", required=True)
    init.add_argument("--base", required=True)
    sub.add_parser("status")
    sub.add_parser("smoke")
    helper = sub.add_parser("helper")
    helper.add_argument("--source", type=Path, required=True)
    revise = sub.add_parser("revise")
    revise.add_argument("--workspace", type=Path, default=ROOT)
    deliver = sub.add_parser("deliver")
    deliver.add_argument("--workspace", type=Path, default=ROOT)
    args = parser.parse_args()
    with dispatcher(ROOT):
        runner = Runner(ROOT)
        try:
            task = Campaign(runner, args.campaign)
            try:
                if args.command == "init":
                    task.init(args.owner, args.base)
                elif args.command == "smoke":
                    task.smoke()
                elif args.command == "deliver":
                    task.deliver(args.workspace.resolve())
                elif args.command == "helper":
                    from .helper_job import implement

                    implement(task, args.source)
                elif args.command == "revise":
                    task.revise(args.workspace)
                print(json.dumps(task.data(), ensure_ascii=True, indent=2), flush=True)
                return 0
            except Stop as error:
                if task.token:
                    task.finish_step(error.state, error.reason)
                print(json.dumps({"state": error.state, "reason": error.reason}), flush=True)
                return 2
            except (OSError, ValueError, subprocess.SubprocessError):
                if task.token:
                    task.finish_step("unknown", "local_operation_unknown_no_replay")
                raise
        finally:
            runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
