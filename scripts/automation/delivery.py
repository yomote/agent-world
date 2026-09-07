"""明示許可されたrunner/helperのjob→review→CI→通常mergeを駆動する。"""

import argparse
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .evidence import thread_id
from .jobs import run_job
from .runner import ROOT, Runner, digest, dispatcher, read_input, safe_path
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
CORRECTIVE_PATHS = (
    "scripts/automation/delivery.py",
    "scripts/automation/helper_job.py",
    "scripts/automation/jobs.py",
    "scripts/tests/test_self_improvement_commit_boundary.py",
    "docs/runbooks/self-improvement.md",
)


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
            CREATE TABLE IF NOT EXISTS delivery_attempts(
                campaign TEXT, attempt INTEGER, data TEXT NOT NULL,
                frozen INTEGER NOT NULL, evidence TEXT NOT NULL,
                PRIMARY KEY(campaign,attempt));
            CREATE TRIGGER IF NOT EXISTS frozen_attempt_update
                BEFORE UPDATE ON delivery_attempts WHEN OLD.frozen=1
                BEGIN SELECT RAISE(ABORT,'frozen_attempt'); END;
            CREATE TRIGGER IF NOT EXISTS frozen_attempt_delete
                BEFORE DELETE ON delivery_attempts WHEN OLD.frozen=1
                BEGIN SELECT RAISE(ABORT,'frozen_attempt'); END;
        """)
        self.token = None
        self.monotonic_deadline = time.monotonic()
        self.transport = Transport(self)
        from .github_adapter import GitHub

        self.transport.github = GitHub(self)

    def data(self):
        row = self.db.execute(
            "SELECT data FROM delivery_campaigns WHERE name=?", (self.name,)
        ).fetchone()
        if row is None:
            raise ValueError("campaign_init_required")
        data = json.loads(row[0])
        data["http_requests"] = self.db.execute(
            "SELECT used FROM delivery_http_budget WHERE id=1"
        ).fetchone()[0]
        data["max_http_requests"] = 30
        return data

    def save(self, data):
        if data.get("attempt_id"):
            changed = self.db.execute(
                "UPDATE delivery_attempts SET data=? WHERE campaign=? AND attempt=? AND frozen=0",
                (json.dumps(data), self.name, data["attempt_id"]),
            ).rowcount
            if changed != 1:
                raise ValueError("attempt_not_mutable")
        self.db.execute(
            "INSERT OR REPLACE INTO delivery_campaigns VALUES (?,?)", (self.name, json.dumps(data))
        )

    def save_event(self, data, kind, *, cancelled_review=None):
        data["updated_at"] = time.time()
        data["last_event"] = kind
        with self.db:
            if cancelled_review is not None:
                changed = self.db.execute(
                    "UPDATE delivery_operations SET state='cancelled_read_only' "
                    "WHERE id=? AND campaign=? AND operation='independent_review' "
                    "AND state='inflight'",
                    (cancelled_review, self.name),
                ).rowcount
                if changed != 1:
                    raise Stop("stopped", "safe_review_resume_not_confirmed")
            self.save(data)
            self.db.execute(
                "INSERT INTO delivery_events(campaign,at,kind,data) VALUES (?,?,?,?)",
                (self.name, time.time(), kind, json.dumps(data)),
            )
        atomic_json(self.directory / "status.json", data)

    @property
    def artifact_directory(self):
        row = self.db.execute(
            "SELECT data FROM delivery_campaigns WHERE name=?", (self.name,)
        ).fetchone()
        attempt = json.loads(row[0]).get("attempt_id") if row else None
        path = self.directory / f"attempt-{attempt}" if attempt else self.directory
        path.mkdir(parents=True, exist_ok=True)
        return path

    def workspace(self):
        return self.root if self.name == "runner" else self.artifact_directory / "checkout"

    def new_attempt(self, data, evidence):
        """明示corrective unit専用。旧raw状態とevent範囲を凍結し、別attemptを作る。"""
        raw = self.db.execute(
            "SELECT data FROM delivery_campaigns WHERE name=?", (self.name,)
        ).fetchone()[0]
        if (
            json.loads(raw).get("attempt_id")
            or self.db.execute(
                "SELECT 1 FROM delivery_attempts WHERE campaign=?", (self.name,)
            ).fetchone()
        ):
            raise Stop("stopped", "explicit_attempt_already_recorded")
        evidence = {
            **evidence,
            "prior_raw_sha256": digest(raw.encode()),
            "prior_event_end": self.db.execute(
                "SELECT MAX(seq) FROM delivery_events WHERE campaign=?", (self.name,)
            ).fetchone()[0],
            "http_used_at_boundary": self.data()["http_requests"],
        }
        data.update(attempt_id=2, previous_attempt=1)
        # 旧attemptの凍結とcurrent projectionの切替は一つのtransaction。
        with self.db:
            self.db.execute(
                "INSERT INTO delivery_attempts VALUES (?,1,?,1,?)",
                (self.name, raw, json.dumps(evidence)),
            )
            self.db.execute(
                "INSERT INTO delivery_attempts VALUES (?,2,?,0,'{}')",
                (self.name, json.dumps(data)),
            )
            self.save(data)
        self.save_event(data, "explicit_attempt_created")

    def corrective(self):
        """通常merge済みrunnerから、この5fileの明示修正を別unitとして納品する。"""
        old = self.data()
        if self.name != "runner" or old["state"] != "merged" or old.get("attempt_id"):
            raise Stop("stopped", "merged_runner_required_for_corrective_unit")
        base, head = old["merge_sha"], git(self.root, "rev-parse", "HEAD")
        self.check_scope(self.root, head, base=base)
        if (
            git(self.root, "merge-base", base, head) != base
            or git(self.root, "branch", "--show-current")
            != "codex/self-improvement-commit-boundary"
            or set(git(self.root, "diff", "--name-only", base, head).splitlines())
            != set(CORRECTIVE_PATHS)
        ):
            raise Stop("stopped", "corrective_scope_or_base_mismatch")
        data = {
            key: old[key]
            for key in (
                "owner",
                "name",
                "cli_starts",
                "max_cli_starts",
                "connector_calls",
                "max_connector_calls",
                "ci_queries",
                "cost_hardcap",
                "model_request_hardcap",
                "upstream_http_hardcap",
                "github_backend",
            )
        }
        data.update(
            base=base,
            integration_base=base,
            head=head,
            pr=None,
            token=None,
            lease_until=None,
            job_owner=old["owner"],
            implementation_source="authorized_primary_owner",
            state="job_verified",
            reason="corrective_fixed_head",
            purpose="commit_boundary",
            deadline=time.time() + 7200,
            last_seen=time.time(),
        )
        self.new_attempt(data, {"kind": "separate_corrective_unit", "prior_merge": base})

    def init(self, owner: str, base: str, *, seconds=None, cli_starts=3, connector_calls=40):
        if self.db.execute(
            "SELECT 1 FROM delivery_campaigns WHERE name=?", (self.name,)
        ).fetchone():
            raise ValueError("campaign_exists_no_budget_reset")
        if seconds is None:
            seconds = 1800 if self.name == "billing-helper" else 7200
        if (
            not SHA.fullmatch(base)
            or not 60 <= seconds <= 7200
            or (self.name == "billing-helper" and seconds != 1800)
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
            upstream_http_hardcap="rest_graphql_shared_30",
            github_backend="native_gcm_rest_graphql",
        )
        if self.name == "billing-helper":
            data.update(campaign_seconds=1800, delivery_attempts=0, max_delivery_attempts=3)
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
            "revision_required",
        }:
            raise Stop(data["state"], data["reason"])
        if self.name == "billing-helper" and (
            data.get("campaign_seconds") != 1800
            or data.get("max_delivery_attempts") != 3
            or type(data.get("delivery_attempts")) is not int
            or not 0 <= data["delivery_attempts"] <= 3
        ):
            self.finish_step("stopped", "helper_packet_not_representable")
            raise Stop("stopped", "helper_packet_not_representable")
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
        data["debrief"] = {
            "outcome": state,
            "reason": reason,
            "head": data.get("head"),
            "job_owner": data.get("job_owner"),
            "pr": data.get("pr"),
            "lesson": "未完了・skip・結果不明を成功にせず、current証拠で判定する。",
        }
        self.save_event(data, reason)

    def resume_review(self, request_id):
        """死んだdispatcherのread-only review待ちだけを再予約可能にする。"""
        data = self.data()
        row = self.db.execute(
            "SELECT id,operation,state,request FROM delivery_operations WHERE campaign=? "
            "ORDER BY rowid DESC LIMIT 1",
            (self.name,),
        ).fetchone()
        if (
            not row
            or row[0] != request_id
            or row[1:3] != ("independent_review", "inflight")
            or data["state"] != "job_verified"
            or data.get("last_event") != "head_fixed"
            or not data["token"]
            or time.time() < data["lease_until"]
            or time.time() >= data["deadline"]
        ):
            raise Stop("stopped", "safe_review_resume_not_confirmed")
        request = json.loads(row[3])
        if request["arguments"]["head"] != data["head"]:
            raise Stop("stopped", "review_resume_head_mismatch")
        workspace = self.workspace()
        if Path(request["arguments"].get("workspace", "")).resolve() != workspace.resolve():
            raise Stop("stopped", "review_resume_workspace_mismatch")
        head = git(workspace, "rev-parse", "HEAD")
        if git(workspace, "merge-base", data["head"], head) != data["head"]:
            raise Stop("stopped", "review_resume_requires_descendant")
        base = git(workspace, "merge-base", "origin/main", head)
        self.check_scope(workspace, head, base=base)
        data.update(
            head=head,
            integration_base=base,
            token=None,
            lease_until=None,
            reason="read_only_review_resumed",
        )
        if data.get("confirmed_prior_push"):
            data["prior_push_revision_head"] = head
        self.save_event(
            data, "interrupted_review_saved_new_request_required", cancelled_review=request_id
        )

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

    def check_scope(self, workspace, head, *, base=None):
        if git(workspace, "status", "--porcelain") or git(workspace, "rev-parse", "HEAD") != head:
            raise Stop("stopped", "head_dirty_or_moved")
        base = base or self.data().get("integration_base", self.data()["base"])
        paths = git(workspace, "diff", "--name-only", base, head).splitlines()
        if self.name == "runner" and self.data().get("purpose") == "commit_boundary":
            if not paths or not set(paths).issubset(CORRECTIVE_PATHS):
                raise Stop("stopped", "corrective_scope_mismatch")
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

    def check_confirmed_revision(self, head, branch):
        data = self.data()
        prior = data.get("confirmed_prior_push")
        if prior and (
            self.name != "runner"
            or head != data["head"]
            or head != data.get("prior_push_revision_head")
            or head == prior["head"]
            or branch != prior["branch"]
        ):
            raise Stop("stopped", "confirmed_push_revision_moved")

    def deliver(self, workspace: Path):
        if self.data()["state"] != "job_verified":
            raise Stop("stopped", "real_job_required_before_delivery")
        self.enter()
        if self.name == "billing-helper":
            data = self.data()
            if data["delivery_attempts"] >= data["max_delivery_attempts"]:
                raise Stop("stopped", "helper_delivery_attempt_budget")
            data["delivery_attempts"] += 1
            self.save_event(data, "helper_delivery_attempt_reserved")
        expected = self.workspace()
        if workspace.resolve() != expected.resolve():
            raise Stop("stopped", "workspace_not_allowed")
        if git(workspace, "remote", "get-url", "origin") != f"https://github.com/{REPOSITORY}.git":
            raise Stop("stopped", "origin_not_allowed")
        head = git(workspace, "rev-parse", "HEAD")
        self.check_confirmed_revision(head, git(workspace, "branch", "--show-current"))
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
        data = self.data()
        data["current_check"] = check
        self.save_event(data, "current_check_pass")
        self.check_scope(workspace, head)
        branch = git(workspace, "branch", "--show-current")
        self.check_confirmed_revision(head, branch)
        if not branch.startswith("codex/"):
            raise Stop("stopped", "branch_not_allowed")
        self.transport.github.git_transfer(workspace, "push", head=head, branch=branch)
        corrective = data.get("purpose") == "commit_boundary"
        summary = (
            "childのGit metadata書込失敗を承認待ちと混同しないため、編集・検証とGit記録を分離。"
            "exact path/hash/baseと実CLI完了を照合した記録だけをjob_verifiedへ進める。"
            "旧Issue28の失敗はimmutableな別attemptに保存し、共有予算と期限を保持する。"
            if corrective
            else "明示起動するbounded改善の実装と証跡。"
        )
        body = (
            summary + "\n\n"
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
                    "title": (
                        "fix: child編集とGit記録の境界を分離する"
                        if corrective
                        else "feat: bounded local improvement " + self.name
                    ),
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
        if not self.data().get("pr_ready"):
            self.transport.call(
                "ready_pr", {"repository_full_name": REPOSITORY, "pr_number": number}, write=True
            )
        data = self.data()
        data["pr_ready"] = True
        self.save_event(data, "ready_pr")
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
            jobs = self.transport.call("current_ci_jobs", {"run_id": latest["id"]})
            nodes = jobs.get("jobs")
            if (
                not isinstance(nodes, list)
                or jobs.get("total_count") != len(nodes)
                or len(nodes) >= 100
            ):
                raise Stop("stopped", "ci_jobs_incomplete")
            checks = [job for job in nodes if job.get("name") == "check"]
            if len(checks) != 1 or checks[0].get("head_sha") != head:
                raise Stop("stopped", "ci_check_identity_missing")
            if checks[0].get("conclusion") == "skipped":
                # Draftのworkflow自体はsuccessになり得る。Readyの実jobを次の照会で待つ。
                continue
            if checks[0].get("status") != "completed" or checks[0].get("conclusion") != "success":
                raise Stop("failed", "current_ci_not_success")
            data = self.data()
            data["ci"] = {
                key: latest.get(key)
                for key in ("id", "head_sha", "run_attempt", "html_url", "conclusion")
            }
            data["ci"]["check_job_id"] = checks[0]["id"]
            data["ci"]["pr_number"] = number
            self.save_event(data, "current_ci_pass")
            return

    def normal_merge(self, head, number):
        protection = self.transport.call("main_protection", {})
        if protection.get("protected") is not True:
            raise Stop("stopped", "main_protection_unverified")
        snapshot = self.transport.call("merge_snapshot", {"pr_number": number})
        pr, comments, nodes = snapshot["pr"], snapshot["comments"], snapshot["threads"]
        from scripts.merge_gate import GateTarget, validate_pr, validate_review

        if not isinstance(comments, list) or len(comments) >= 100:
            raise Stop("stopped", "review_comments_unverified")
        try:
            validate_pr(pr, GateTarget(number, head))
            observed_reviewer = validate_review(comments, head)
        except RuntimeError as error:
            raise Stop("stopped", "current_review_unverified") from error
        if observed_reviewer != REVIEWER:
            raise Stop("stopped", "reviewer_mismatch")
        if (
            not isinstance(nodes, list)
            or len(nodes) >= 100
            or any(node.get("isResolved") is not True for node in nodes)
        ):
            raise Stop("stopped", "review_threads_unverified")
        data = self.data()
        data.update(
            merge_validated_head=head,
            merge_validated_at=time.time(),
            merge_snapshot=snapshot,
            protection={
                "protected": True,
                "mode": "standard_rest_squash_expected_sha",
                "hidden_bypass_configuration": "not_verified",
            },
        )
        self.save_event(data, "merge_packet_validated")
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
            "mode": "standard_rest_squash_expected_sha",
            "hidden_bypass_configuration": "not_verified",
        }
        self.save_event(data, "normal_merge_confirmed")
        if data.get("issue"):
            closed = self.transport.call(
                "close_issue",
                {
                    "repository_full_name": REPOSITORY,
                    "issue_number": data["issue"],
                    "state": "closed",
                    "state_reason": "completed",
                },
                write=True,
            )
            if closed.get("number") != data["issue"] or closed.get("state") != "closed":
                raise Stop("unknown", "issue_close_unverified")
            self.finish_step("completed", "issue_completed_after_normal_merge")
            return
        self.finish_step("merged", "normal_protected_merge_confirmed")

    def reconcile_push(self):
        """今回確認済みのca9 pushだけを照合。ネットワーク照会・再送はしない。"""
        data = self.data()
        prior = "ca9b64e8c51d047324c07a918a2d11472512daab"
        branch = "codex/self-improvement-runner"
        if (
            self.name != "runner"
            or data["state"] != "job_verified"
            or data["reason"] != "revised_head"
            or data["head"] != prior
            or data.get("last_event") != "git_push_reserved"
            or data.get("pr") is not None
            or data.get("confirmed_prior_push")
            or not data.get("token")
            or time.time() < data["lease_until"]
            or time.time() >= data["deadline"]
            or data["http_requests"] != 0
            or self.db.execute("SELECT 1 FROM delivery_http_requests LIMIT 1").fetchone()
            or self.db.execute(
                "SELECT 1 FROM delivery_operations WHERE campaign=? "
                "AND (operation NOT IN ('independent_review','current_check') "
                "OR state NOT IN ('ok','cancelled_read_only')) LIMIT 1",
                (self.name,),
            ).fetchone()
        ):
            raise Stop("stopped", "prior_push_boundary_mismatch")
        raw = read_input(self.root, "artifacts/self-improvement/confirmed-prior-push.json")
        evidence = json.loads(raw.decode("utf-8-sig"))
        if (
            set(evidence)
            != {
                "operation",
                "branch",
                "reserved_head",
                "remote_head",
                "verification",
                "observed_at_utc_window",
                "retry_performed",
            }
            or evidence["operation"] != "git_push"
            or evidence["branch"] != branch
            or evidence["reserved_head"] != prior
            or evidence["remote_head"] != prior
            or evidence["verification"] != "single_read_only_git_ls_remote"
            or evidence["retry_performed"] is not False
        ):
            raise Stop("stopped", "prior_push_evidence_mismatch")
        window = evidence["observed_at_utc_window"]
        if set(window) != {"after", "before"} or any(
            not isinstance(value, str) or not value.endswith("Z") for value in window.values()
        ):
            raise Stop("stopped", "prior_push_observation_window")
        after, before = (
            datetime.fromisoformat(window[key].replace("Z", "+00:00")).timestamp()
            for key in ("after", "before")
        )
        reserved = self.db.execute(
            "SELECT kind,at,data FROM delivery_events WHERE campaign=? ORDER BY seq DESC LIMIT 1",
            (self.name,),
        ).fetchone()
        if (
            not reserved
            or reserved[0] != "git_push_reserved"
            or json.loads(reserved[2])["head"] != prior
            or not reserved[1] <= after <= before <= time.time()
        ):
            raise Stop("stopped", "prior_push_observation_window")
        remote = f"https://github.com/{REPOSITORY}.git"
        if (
            git(self.root, "remote", "get-url", "origin") != remote
            or git(self.root, "branch", "--show-current") != branch
        ):
            raise Stop("stopped", "prior_push_target_mismatch")
        data.update(
            state="revision_required",
            reason="confirmed_prior_push_requires_new_head",
            token=None,
            lease_until=None,
            confirmed_prior_push={
                "head": prior,
                "branch": branch,
                "ref": f"refs/heads/{branch}",
                "remote": remote,
                "evidence_sha256": digest(raw),
                "observation": evidence,
            },
        )
        self.save_event(data, "prior_push_confirmed_no_replay")

    def revise(self, workspace):
        data = self.data()
        reconciled = (
            data["state"] == "revision_required"
            and data["reason"] == "confirmed_prior_push_requires_new_head"
            and data.get("confirmed_prior_push", {}).get("head") == data["head"]
        )
        if not reconciled and (
            data["state"] != "failed"
            or data["reason"]
            not in {
                "independent_review_not_pass",
                "current_check_not_pass",
                "current_ci_not_success",
            }
        ):
            raise Stop(data["state"], "revision_not_allowed")
        head = git(workspace, "rev-parse", "HEAD")
        if head == data["head"] or git(workspace, "merge-base", data["head"], head) != data["head"]:
            raise Stop("failed", "revision_requires_descendant_head")
        if data.get("confirmed_prior_push") and (
            workspace.resolve() != self.root.resolve()
            or not git(workspace, "diff", "--name-only", data["head"], head)
        ):
            raise Stop("stopped", "prior_push_requires_meaningful_revision")
        base = git(workspace, "merge-base", "origin/main", head)
        self.check_scope(workspace, head, base=base)
        data.update(state="job_verified", reason="revised_head", head=head, integration_base=base)
        if data.get("confirmed_prior_push"):
            data["prior_push_revision_head"] = head
            data.pop("review", None)
            data.pop("current_check", None)
        self.save_event(data, "revised_head_same_budget")

    def handoff(self, reviewed_head):
        """受領済みのbackend置換境界だけを適用。未知writeの一般的resumeではない。"""
        data = self.data()
        expected = "d1baecb6cd85847ac6976e2cd68f277b80894a87"
        if (
            self.name != "runner"
            or reviewed_head != expected
            or data["head"] != expected
            or data["state"] != "job_verified"
            or data.get("pr") is not None
            or data.get("last_event") != "push_reserved"
            or data.get("handoff_applied")
            or time.time() >= data["deadline"]
        ):
            raise Stop("stopped", "handoff_boundary_mismatch")
        operations = [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT response FROM delivery_operations WHERE campaign=? ORDER BY rowid",
                (self.name,),
            )
            if row[0]
        ]
        if (
            len(operations) != 4
            or operations[-1]["result"].get("head") != expected
            or operations[-1]["result"].get("exit_code") != 0
            or operations[-2]["result"].get("verdict") != "pass"
        ):
            raise Stop("stopped", "handoff_evidence_mismatch")
        head = git(self.root, "rev-parse", "HEAD")
        base = git(self.root, "merge-base", "origin/main", head)
        self.check_scope(self.root, head, base=base)
        data.update(
            head=head,
            integration_base=base,
            token=None,
            lease_until=None,
            handoff_applied="user-confirmed-before-github-write",
            reason="backend_handoff",
            github_backend="native_gcm_rest_graphql",
            upstream_http_hardcap="rest_graphql_shared_30",
        )
        data.pop("review", None)
        data.pop("current_check", None)
        self.save_event(data, "received_handoff_new_head_requires_review")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", choices=SCOPES)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--owner", required=True)
    init.add_argument("--base", required=True)
    sub.add_parser("status")
    sub.add_parser("smoke")
    sub.add_parser("reconcile-push")
    sub.add_parser("corrective")
    sub.add_parser("new-helper-attempt")
    sub.add_parser("record-commit")
    sub.add_parser("reconcile-commit")
    handoff = sub.add_parser("handoff")
    handoff.add_argument("--reviewed-head", required=True)
    resume_review = sub.add_parser("resume-review")
    resume_review.add_argument("--request-id", required=True)
    helper = sub.add_parser("helper")
    helper.add_argument("--source", type=Path, required=True)
    revise = sub.add_parser("revise")
    revise.add_argument("--workspace", type=Path, default=ROOT)
    deliver = sub.add_parser("deliver")
    deliver.add_argument("--workspace", type=Path, default=ROOT)
    deliver.add_argument("--helper-source", type=Path)
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
                    if args.helper_source:
                        if args.campaign != "runner" or task.data()["state"] != "merged":
                            raise Stop("stopped", "runner_merge_required")
                        parent = task.data()
                        task = Campaign(runner, "billing-helper")
                        task.init(parent["owner"], parent["merge_sha"], seconds=1800)
                        from .helper_job import implement

                        implement(task, args.helper_source)
                        # childの編集完了はGit記録待ち。成功receiptを後付けしてdeliveryしない。
                elif args.command == "helper":
                    from .helper_job import implement

                    implement(task, args.source)
                elif args.command == "corrective":
                    task.corrective()
                elif args.command in {"new-helper-attempt", "record-commit", "reconcile-commit"}:
                    from .helper_job import new_attempt, reconcile_commit, record_commit

                    {
                        "new-helper-attempt": new_attempt,
                        "record-commit": record_commit,
                        "reconcile-commit": reconcile_commit,
                    }[args.command](task)
                elif args.command == "revise":
                    task.revise(args.workspace)
                elif args.command == "handoff":
                    task.handoff(args.reviewed_head)
                elif args.command == "resume-review":
                    task.resume_review(args.request_id)
                elif args.command == "reconcile-push":
                    task.reconcile_push()
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
