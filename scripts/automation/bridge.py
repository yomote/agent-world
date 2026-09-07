"""既存connectorへの配送を一度だけclaimする。成功stateの後付け注入は行わない。"""

import argparse
import json
import sqlite3
import time
from uuid import UUID

from .runner import ROOT, STATE, safe_path

OPERATIONS = {
    "independent_review",
    "current_check",
    "create_draft_pr",
    "post_review_evidence",
    "ready_pr",
    "current_ci",
    "current_ci_jobs",
    "pr_info",
    "review_threads",
    "normal_merge",
    "main_protection",
    "review_comments",
    "create_helper_issue",
    "close_issue",
}


def take(root, campaign):
    directory = safe_path(root, f"{STATE}/{campaign}/transport")
    path = directory / "request.json"
    if not path.exists():
        return None
    identifier = str(UUID(json.loads(path.read_text(encoding="utf-8"))["id"]))
    with sqlite3.connect(f"file:{root / STATE / 'state.sqlite3'}?mode=ro", uri=True) as db:
        row = db.execute("SELECT data FROM delivery_campaigns WHERE name=?", (campaign,)).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        if not data["token"] or time.time() >= min(data["lease_until"], data["deadline"]):
            return None
        row = db.execute(
            "SELECT request,state FROM delivery_operations WHERE id=? AND campaign=?",
            (identifier, campaign),
        ).fetchone()
        if row is None or row[1] != "inflight":
            return None
        request = json.loads(row[0])
    if request["operation"] not in OPERATIONS:
        raise ValueError("unsupported_transport_operation")
    try:
        with (directory / (identifier + ".sent.json")).open("x", encoding="utf-8") as output:
            json.dump({"id": identifier, "claimed_at": time.time()}, output)
    except FileExistsError:
        return None
    return request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", choices=("runner", "billing-helper"))
    parser.add_argument("command", choices=("take",))
    args = parser.parse_args()
    print(json.dumps(take(ROOT, args.campaign), ensure_ascii=True))


if __name__ == "__main__":
    main()
