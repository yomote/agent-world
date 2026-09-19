"""通常PM運用から二段階PR reviewを配送するbounded standalone入口。"""

import argparse
import json
import sqlite3
import time
from pathlib import Path

from .github_adapter import REPOSITORY, SHA, GitHub
from .review_delivery import apply_visibility_postcondition, validate_review_input
from .transport import Stop


class AdapterTask:
    """campaignを偽装せず、adapterのbudget/receiptを同じDBへ記録する。"""

    def __init__(self, root, db, name):
        self.root, self.db, self.name = root, db, name
        self._data = {"deadline": time.time() + 120}

    def boundary(self):
        if time.time() >= self._data["deadline"]:
            raise Stop("stopped", "standalone_review_deadline")

    def data(self):
        return dict(self._data)

    def save_event(self, value, reason):
        self._data = dict(value)
        self._data["last_event"] = reason


class StandaloneReviewDelivery:
    def __init__(self, root, state_path, *, github=None):
        self.root = Path(root).resolve()
        self.db = sqlite3.connect(state_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS standalone_review_stages(
                pr INTEGER,head TEXT,state TEXT,initial_json TEXT,current_json TEXT,
                prior_receipt_json TEXT,final_review_json TEXT,final_receipt_json TEXT,
                updated_at REAL,PRIMARY KEY(pr,head));
        """)
        self.task = AdapterTask(self.root, self.db, "standalone-pr-review-delivery")
        self.github = github or GitHub(self.task)

    def close(self):
        self.db.close()

    def _row(self, pr, head):
        return self.db.execute(
            "SELECT * FROM standalone_review_stages WHERE pr=? AND head=?", (pr, head)
        ).fetchone()

    @staticmethod
    def _read(path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise Stop("stopped", "standalone_review_input_invalid") from None

    def publish_initial(self, pr, head, source):
        review = self._read(source)
        validate_review_input(review)
        if review.get("head") != head or not SHA.fullmatch(head):
            raise Stop("stopped", "standalone_review_head_mismatch")
        declared = {item["acceptance_id"] for item in review.get("live_postconditions", [])}
        statuses = {item["acceptance_id"]: item["status"] for item in review["acceptance_map"]}
        if not declared or any(statuses[item] != "unknown" for item in declared):
            raise Stop("stopped", "standalone_review_postcondition_not_pending")
        if self._row(pr, head):
            raise Stop("stopped", "standalone_review_stage_exists")
        frozen = json.dumps(review, ensure_ascii=False, sort_keys=True)
        with self.db:
            self.db.execute(
                "INSERT INTO standalone_review_stages VALUES (?,?,?,?,?,?,?,?,?)",
                (pr, head, "initial_reserved", frozen, frozen, None, None, None, time.time()),
            )
        arguments = self._arguments(pr, head, review)
        try:
            receipt = self.github.call("publish_pr_review", arguments)
        except Stop as error:
            state = "initial_unknown" if error.state == "unknown" else error.state
            self._transition_if(pr, head, "initial_reserved", state)
            raise
        self._save_receipt(
            pr,
            head,
            "postcondition_pending",
            receipt,
            final=False,
            expected=("initial_reserved",),
        )
        return receipt

    def reconcile_initial(self, pr, head):
        row = self._require_one_of(pr, head, ("initial_reserved", "initial_unknown"))
        review = json.loads(row["initial_json"])
        receipt = self.github.call("reconcile_pr_review", self._arguments(pr, head, review))
        self._save_receipt(
            pr,
            head,
            "postcondition_pending",
            receipt,
            final=False,
            expected=("initial_reserved", "initial_unknown"),
        )
        return receipt

    def publish_final(self, pr, head, source):
        row = self._require(pr, head, "postcondition_pending")
        packet = self._read(source)
        if set(packet) != {"head", "pr_number", "receipt_url", "ui_evidence", "review_input"}:
            raise Stop("stopped", "standalone_postcondition_input_invalid")
        if packet["head"] != head or packet["pr_number"] != pr:
            raise Stop("stopped", "standalone_postcondition_target_mismatch")
        initial = json.loads(row["initial_json"])
        prior = json.loads(row["prior_receipt_json"])
        if packet["receipt_url"] != prior["url"]:
            raise Stop("stopped", "standalone_postcondition_target_mismatch")
        updated = apply_visibility_postcondition(
            initial,
            packet["review_input"],
            head=head,
            pr_number=pr,
            receipt=prior,
            ui_evidence=packet["ui_evidence"],
        )
        final_review = dict(updated)
        final_review["checks"] = [
            *initial["checks"],
            "trusted local operator observed the prior COMMENT review in PR UI; not an auth role",
        ]
        final_json = json.dumps(final_review, ensure_ascii=False, sort_keys=True)
        with self.db:
            cursor = self.db.execute(
                "UPDATE standalone_review_stages SET state='final_reserved',current_json=?,"
                "final_review_json=?,updated_at=? WHERE pr=? AND head=? "
                "AND state='postcondition_pending'",
                (
                    json.dumps(updated, ensure_ascii=False, sort_keys=True),
                    final_json,
                    time.time(),
                    pr,
                    head,
                ),
            )
        if cursor.rowcount != 1:
            raise Stop("stopped", "standalone_review_stage_conflict")
        try:
            receipt = self.github.call("publish_pr_review", self._arguments(pr, head, final_review))
        except Stop as error:
            state = "final_unknown" if error.state == "unknown" else error.state
            self._transition_if(pr, head, "final_reserved", state)
            raise
        self._save_receipt(pr, head, "complete", receipt, final=True, expected=("final_reserved",))
        return receipt

    def reconcile_final(self, pr, head):
        row = self._require_one_of(pr, head, ("final_reserved", "final_unknown"))
        review = json.loads(row["final_review_json"])
        receipt = self.github.call("reconcile_pr_review", self._arguments(pr, head, review))
        self._save_receipt(
            pr,
            head,
            "complete",
            receipt,
            final=True,
            expected=("final_reserved", "final_unknown"),
        )
        return receipt

    def _require(self, pr, head, state):
        row = self._row(pr, head)
        if row is None or row["state"] != state:
            raise Stop("stopped", "standalone_review_stage_invalid")
        return row

    def _require_one_of(self, pr, head, states):
        row = self._row(pr, head)
        if row is None or row["state"] not in states:
            raise Stop("stopped", "standalone_review_stage_invalid")
        return row

    @staticmethod
    def _arguments(pr, head, review):
        return {
            "repository_full_name": REPOSITORY,
            "pr_number": pr,
            "head": head,
            "review": review,
        }

    def _transition_if(self, pr, head, expected, state):
        with self.db:
            self.db.execute(
                "UPDATE standalone_review_stages SET state=?,updated_at=? "
                "WHERE pr=? AND head=? AND state=?",
                (state, time.time(), pr, head, expected),
            )

    def _save_receipt(self, pr, head, state, receipt, *, final, expected):
        if receipt.get("head") != head:
            raise Stop("unknown", "standalone_review_receipt_invalid")
        field = "final_receipt_json" if final else "prior_receipt_json"
        placeholders = ",".join("?" for _ in expected)
        with self.db:
            cursor = self.db.execute(
                f"UPDATE standalone_review_stages SET state=?,{field}=?,updated_at=? "
                f"WHERE pr=? AND head=? AND state IN ({placeholders})",
                (state, json.dumps(receipt, sort_keys=True), time.time(), pr, head, *expected),
            )
        if cursor.rowcount != 1:
            raise Stop("unknown", "standalone_review_stage_conflict")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("initial", "reconcile-initial", "final", "reconcile-final")
    )
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    delivery = StandaloneReviewDelivery(args.root, args.state)
    try:
        if args.command in {"initial", "final"} and args.source is None:
            raise Stop("stopped", "standalone_review_source_required")
        result = {
            "initial": lambda: delivery.publish_initial(args.pr, args.head, args.source),
            "reconcile-initial": lambda: delivery.reconcile_initial(args.pr, args.head),
            "final": lambda: delivery.publish_final(args.pr, args.head, args.source),
            "reconcile-final": lambda: delivery.reconcile_final(args.pr, args.head),
        }[args.command]()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Stop as error:
        print(json.dumps({"state": error.state, "reason": error.reason}))
        return 2
    finally:
        delivery.close()


if __name__ == "__main__":
    raise SystemExit(main())
