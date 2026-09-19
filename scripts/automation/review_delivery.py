"""独立reviewerの結果を通常のGitHub PR reviewへ安全に写像する。"""

from __future__ import annotations

import hashlib
import json
import re

from .transport import Stop

SHA = re.compile(r"[0-9a-f]{40}\Z")
PATH = re.compile(r"(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\\\x00]+\Z")
FINDING_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
SEVERITIES = {"P0", "P1", "P2"}
CATEGORIES = {"generic_risk", "business_invariant"}
SUPPRESSION_REASONS = {"duplicate", "stale", "preference", "out_of_scope", "backlog"}
MARKER = "agent-world-review-delivery"


def _text(value, name, *, limit=4000):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise Stop("stopped", f"review_{name}_invalid")
    if len(value) > limit:
        raise Stop("stopped", f"review_{name}_invalid")
    return value


def changed_lines(patch):
    """GitHubのunified diffからLEFT/RIGHTそれぞれにcomment可能な行を得る。"""
    if not isinstance(patch, str):
        return {"LEFT": set(), "RIGHT": set()}
    result = {"LEFT": set(), "RIGHT": set()}
    old = new = None
    for raw in patch.splitlines():
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if match:
            old, new = map(int, match.groups())
            continue
        if old is None or not raw or raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            result["RIGHT"].add(new)
            new += 1
        elif raw.startswith("-"):
            result["LEFT"].add(old)
            old += 1
        else:
            result["LEFT"].add(old)
            result["RIGHT"].add(new)
            old += 1
            new += 1
    return result


def _finding_body(finding):
    return (
        f"<!-- {MARKER}-finding:{finding['id']} -->\n"
        f"**{finding['severity']} / {finding['category']}** · "
        f"requirement `{finding['requirement_id']}` · acceptance `{finding['acceptance_id']}`\n\n"
        f"**問題**: {finding['problem']}\n\n"
        f"**失敗シナリオ**: {finding['failure_scenario']}\n\n"
        f"**影響**: {finding['impact']}\n\n"
        f"**根拠**: {finding['evidence']}\n\n"
        f"**要求する対応**: {finding['request']}\n\n"
        f"**blocking根拠**: {finding['blocking_rationale']}"
    )


def validate_review_input(value):
    """Issue責任者のAC mapと作者のreview観点を、review前に固定する。"""
    if not isinstance(value, dict):
        raise Stop("stopped", "review_input_missing")
    scope_id = _text(value.get("scope_id"), "scope_id", limit=200)
    scope_issue = _text(value.get("scope_issue"), "scope_issue", limit=50)
    if not re.fullmatch(r"#[1-9][0-9]*", scope_issue):
        raise Stop("stopped", "review_scope_issue_invalid")
    _text(value.get("scope_definition"), "scope_definition")
    acceptance_map = value.get("acceptance_map")
    if not isinstance(acceptance_map, list) or not acceptance_map:
        raise Stop("stopped", "review_acceptance_map_missing")
    acceptance_pairs = set()
    for item in acceptance_map:
        if not isinstance(item, dict) or item.get("status") not in {
            "achieved",
            "unmet",
            "unknown",
        }:
            raise Stop("stopped", "review_acceptance_map_invalid")
        requirement = _text(item.get("requirement_id"), "requirement_id", limit=200)
        acceptance = _text(item.get("acceptance_id"), "acceptance_id", limit=200)
        _text(item.get("issue"), "acceptance_issue", limit=200)
        _text(item.get("pm_owner"), "acceptance_pm_owner", limit=200)
        _text(item.get("source"), "acceptance_source", limit=500)
        _text(item.get("source_version"), "acceptance_source_version", limit=200)
        _text(item.get("definition"), "acceptance_definition")
        _text(item.get("evidence"), "acceptance_evidence")
        acceptance_pairs.add((requirement, acceptance))
    acceptance_ids = [item["acceptance_id"] for item in acceptance_map]
    if len(set(acceptance_ids)) != len(acceptance_ids):
        raise Stop("stopped", "review_acceptance_id_duplicate")
    author_plan = value.get("author_review_plan")
    if not isinstance(author_plan, list) or not author_plan:
        raise Stop("stopped", "review_author_plan_missing")
    for item in author_plan:
        if not isinstance(item, dict) or item.get("category") not in CATEGORIES:
            raise Stop("stopped", "review_author_plan_invalid")
        for name in ("id", "focus", "evidence", "known_unmet"):
            _text(item.get(name), "author_plan_" + name)
    required = value.get("required_acceptance_ids")
    if (
        not isinstance(required, list)
        or not required
        or len(set(required)) != len(required)
        or any(not isinstance(identifier, str) or not identifier for identifier in required)
        or not set(required).issubset(set(acceptance_ids))
    ):
        raise Stop("stopped", "review_required_acceptance_invalid")
    if any(
        item["acceptance_id"] in required and item["issue"] != scope_issue
        for item in acceptance_map
    ):
        raise Stop("stopped", "review_required_acceptance_scope_mismatch")
    residuals = value.get("parent_residuals")
    if not isinstance(residuals, list):
        raise Stop("stopped", "review_parent_residuals_invalid")
    for item in residuals:
        if not isinstance(item, dict):
            raise Stop("stopped", "review_parent_residuals_invalid")
        for name in ("id", "issue", "owner", "trigger"):
            _text(item.get(name), "parent_residual_" + name)
        if not re.fullmatch(r"#[1-9][0-9]*", item["issue"]):
            raise Stop("stopped", "review_parent_residual_issue_invalid")
    allowed_issues = {scope_issue, *(item["issue"] for item in residuals)}
    if any(item["issue"] not in allowed_issues for item in acceptance_map):
        raise Stop("stopped", "review_acceptance_issue_unbound")
    live_postconditions = value.get("live_postconditions", [])
    if not isinstance(live_postconditions, list):
        raise Stop("stopped", "review_live_postconditions_invalid")
    postcondition_ids = []
    for item in live_postconditions:
        if (
            not isinstance(item, dict)
            or item.get("kind") != "github_review_visibility"
            or set(item) != {"acceptance_id", "kind"}
        ):
            raise Stop("stopped", "review_live_postconditions_invalid")
        postcondition_ids.append(_text(item.get("acceptance_id"), "postcondition_acceptance_id"))
    if len(set(postcondition_ids)) != len(postcondition_ids) or not set(postcondition_ids).issubset(
        set(required)
    ):
        raise Stop("stopped", "review_live_postconditions_invalid")
    return acceptance_map, author_plan, acceptance_pairs, required, scope_id, scope_issue


def acceptance_gate(value):
    acceptance_map, _, _, required, scope_id, scope_issue = validate_review_input(value)
    by_id = {item["acceptance_id"]: item for item in acceptance_map}
    unmet = [
        {
            "acceptance_id": identifier,
            "status": by_id[identifier]["status"],
            "pm_owner": by_id[identifier]["pm_owner"],
            "next_evidence": by_id[identifier]["evidence"],
        }
        for identifier in required
        if by_id[identifier]["status"] != "achieved"
    ]
    return {
        "scope_id": scope_id,
        "scope_issue": scope_issue,
        "required": required,
        "unmet": unmet,
        "ready": not unmet,
    }


def issue_close_gate(value, issue_number):
    """PR scopeが完了したIssueだけを閉じ、親の残DoDを保持する。"""
    _, _, _, _, _, scope_issue = validate_review_input(value)
    scope_number = int(scope_issue[1:])
    residual_numbers = sorted(int(item["issue"][1:]) for item in value["parent_residuals"])
    may_close = issue_number == scope_number and issue_number not in residual_numbers
    return {
        "may_close": may_close,
        "reason": None if may_close else "issue_not_completed_by_pr_scope",
        "scope_issue": scope_issue,
        "parent_residual_issues": residual_numbers,
    }


def apply_visibility_postcondition(original, update, *, head, pr_number, receipt, ui_evidence):
    """事前宣言したGitHub review可視性だけをunknownからachievedへ進める。"""
    validate_review_input(original)
    validate_review_input(update)
    immutable = set(original) - {"acceptance_map"}
    if immutable != set(update) - {"acceptance_map"} or any(
        original[name] != update[name] for name in immutable
    ):
        raise Stop("stopped", "review_postcondition_contract_changed")
    if (
        receipt.get("head") != head
        or receipt.get("url") != ui_evidence.get("review_url")
        or ui_evidence.get("head") != head
        or ui_evidence.get("pr_number") != pr_number
        or ui_evidence.get("actor") != receipt.get("actor")
        or ui_evidence.get("state") != "COMMENTED"
        or receipt.get("state") != "COMMENTED"
        or ui_evidence.get("surfaces") != ["conversation", "files_changed"]
        or not re.fullmatch(
            rf"https://github\.com/yomote/agent-world/pull/{pr_number}#pullrequestreview-[1-9][0-9]*",
            receipt.get("url", ""),
        )
    ):
        raise Stop("stopped", "review_postcondition_evidence_invalid")
    declared = {item["acceptance_id"] for item in original.get("live_postconditions", [])}
    old_by_id = {item["acceptance_id"]: item for item in original["acceptance_map"]}
    new_by_id = {item["acceptance_id"]: item for item in update["acceptance_map"]}
    if set(old_by_id) != set(new_by_id):
        raise Stop("stopped", "review_postcondition_contract_changed")
    changed = []
    for identifier, old in old_by_id.items():
        new = new_by_id[identifier]
        stable = set(old) - {"status", "evidence"}
        if stable != set(new) - {"status", "evidence"} or any(
            old[name] != new[name] for name in stable
        ):
            raise Stop("stopped", "review_postcondition_contract_changed")
        if old != new:
            if (
                identifier not in declared
                or old["status"] != "unknown"
                or new["status"] != "achieved"
                or receipt["url"] not in new["evidence"]
            ):
                raise Stop("stopped", "review_postcondition_transition_invalid")
            changed.append(identifier)
    if set(changed) != declared or not acceptance_gate(update)["ready"]:
        raise Stop("stopped", "review_postcondition_transition_incomplete")
    return update


def render_review_input(value):
    """PR作者が公開するAC対応と変更固有review観点を短いMarkdownへする。"""
    acceptance_map, author_plan, _, _, scope_id, scope_issue = validate_review_input(value)
    lines = [
        f"## PR scope `{scope_id}` / `{scope_issue}` のAC対応（Issue責任者 / PM管理）",
        "",
    ]
    for item in acceptance_map:
        lines.append(
            f"- `{item['requirement_id']} / {item['acceptance_id']}` "
            f"[{item['status']}] {item['definition']} — evidence: {item['evidence']} "
            f"([source]({item['source']}), version `{item['source_version']}`, "
            f"PM `{item['pm_owner']}`)"
        )
    lines.extend(["", "## PR作者の変更固有review観点", ""])
    for item in author_plan:
        lines.append(
            f"- `{item['id']}` / {item['category']}: {item['focus']} — "
            f"evidence: {item['evidence']} / known unmet: {item['known_unmet']}"
        )
    if value["parent_residuals"]:
        lines.extend(["", "## 親Issueに残るDoD", ""])
        for item in value["parent_residuals"]:
            lines.append(
                f"- `{item['id']}` / `{item['issue']}`: owner `{item['owner']}` / "
                f"trigger: {item['trigger']}"
            )
    if value.get("live_postconditions"):
        lines.extend(["", "## 投稿後に確認するlive postcondition", ""])
        for item in value["live_postconditions"]:
            lines.append(
                f"- `{item['acceptance_id']}`: GitHub COMMENT reviewのvisible receiptとPR UIを"
                "PM指示のtrusted local operatorが確認する（認証roleの保証ではない）"
            )
    lines.extend(
        [
            "",
            "この対応表は独立reviewerの確認範囲を制限せず、作者の自己申告だけでAC達成にしません。",
        ]
    )
    return "\n".join(lines)


def review_key(review, head):
    canonical = {
        "head": head,
        "reviewer": review.get("reviewer"),
        "scope": review.get("scope"),
        "checks": review.get("checks"),
        "verdict": review.get("verdict"),
        "findings": review.get("findings"),
        "suppressed": review.get("suppressed", []),
        "acceptance_map": review.get("acceptance_map"),
        "author_review_plan": review.get("author_review_plan"),
        "scope_id": review.get("scope_id"),
        "scope_issue": review.get("scope_issue"),
        "scope_definition": review.get("scope_definition"),
        "required_acceptance_ids": review.get("required_acceptance_ids"),
        "parent_residuals": review.get("parent_residuals"),
        "live_postconditions": review.get("live_postconditions", []),
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def prepare(review, *, head, files, proxy_login, pr_author):
    """reviewer結果を検証し、GitHub COMMENT review payloadとreceipt keyを作る。"""
    if not SHA.fullmatch(head or "") or review.get("head") != head:
        raise Stop("stopped", "review_head_mismatch")
    if review.get("event", "COMMENT") != "COMMENT":
        raise Stop("stopped", "review_event_must_be_comment")
    reviewer = _text(review.get("reviewer"), "reviewer", limit=200)
    scope = _text(review.get("scope"), "scope")
    checks = review.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or any(not isinstance(item, str) or not item.strip() for item in checks)
    ):
        raise Stop("stopped", "review_checks_invalid")
    if review.get("verdict") not in {"pass", "fail"}:
        raise Stop("stopped", "review_verdict_invalid")
    _text(proxy_login, "proxy_login", limit=100)
    _text(pr_author, "pr_author", limit=100)
    acceptance_map, author_plan, acceptance_pairs, _, _, _ = validate_review_input(review)

    file_lines = {}
    for item in files:
        path = item.get("filename") if isinstance(item, dict) else None
        if isinstance(path, str):
            file_lines[path] = changed_lines(item.get("patch"))

    comments, ids = [], set()
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise Stop("stopped", "review_findings_invalid")
    for finding in findings:
        if not isinstance(finding, dict):
            raise Stop("stopped", "review_finding_invalid")
        identifier = finding.get("id")
        if not FINDING_ID.fullmatch(identifier or "") or identifier in ids:
            raise Stop("stopped", "review_finding_id_invalid")
        ids.add(identifier)
        path, side, line = finding.get("path"), finding.get("side"), finding.get("line")
        start = finding.get("start_line", line)
        start_side = finding.get("start_side", side)
        if (
            not PATH.fullmatch(path or "")
            or side not in {"LEFT", "RIGHT"}
            or start_side != side
            or type(line) is not int
            or type(start) is not int
            or start > line
            or line < 1
            or path not in file_lines
            or any(number not in file_lines[path][side] for number in range(start, line + 1))
        ):
            raise Stop("stopped", "review_finding_location_invalid")
        if finding.get("severity") not in SEVERITIES:
            raise Stop("stopped", "review_finding_severity_invalid")
        if finding.get("category") not in CATEGORIES:
            raise Stop("stopped", "review_finding_category_invalid")
        for name in (
            "requirement_id",
            "acceptance_id",
            "problem",
            "failure_scenario",
            "impact",
            "evidence",
            "request",
            "blocking_rationale",
        ):
            _text(finding.get(name), "finding_" + name)
        if (finding["requirement_id"], finding["acceptance_id"]) not in acceptance_pairs:
            raise Stop("stopped", "review_finding_acceptance_unmapped")
        comment = {"path": path, "line": line, "side": side, "body": _finding_body(finding)}
        if start != line:
            comment.update(start_line=start, start_side=side)
        comments.append(comment)

    suppressed = review.get("suppressed", [])
    if not isinstance(suppressed, list):
        raise Stop("stopped", "review_suppressed_invalid")
    suppression_lines = []
    for item in suppressed:
        if (
            not isinstance(item, dict)
            or not FINDING_ID.fullmatch(item.get("id", ""))
            or item.get("reason") not in SUPPRESSION_REASONS
        ):
            raise Stop("stopped", "review_suppressed_invalid")
        explanation = _text(item.get("explanation"), "suppressed_explanation")
        suppression_lines.append(f"- `{item['id']}`: {item['reason']} — {explanation}")

    if (review["verdict"] == "pass") != (len(findings) == 0):
        raise Stop("stopped", "review_verdict_findings_mismatch")
    key = review_key(review, head)
    body = (
        f"<!-- {MARKER}:{key} -->\n"
        "## 独立レビュー配送\n\n"
        f"- 対象head: `{head}`\n"
        f"- 独立reviewer: `{reviewer}`\n"
        f"- scope: {scope}\n"
        f"- checks: {'; '.join(checks)}\n"
        f"- findings: {len(findings)}\n"
        f"- verdict: {review['verdict']}\n\n"
        f"- AC map: {len(acceptance_map)}件（Issue責任者/PMの原ACと状態を参照）\n"
        f"- 作者review観点: {len(author_plan)}件（独立reviewerを拘束しない）\n\n"
        f"このreviewは `{proxy_login}` の既存GitHub認証が独立reviewerの結果を代行記録した"
        f"COMMENTです（PR author: `{pr_author}`）。GitHub上のAPPROVE/CHANGES_REQUESTEDや"
        "reviewer本人の認証を表しません。CI成功だけを目的達成の根拠にしていません。"
    )
    body += "\n\n" + render_review_input(review)
    if suppression_lines:
        body += "\n\n### inlineにしなかった項目\n\n" + "\n".join(suppression_lines)
    return {
        "key": key,
        "payload": {"commit_id": head, "body": body, "event": "COMMENT", "comments": comments},
        "finding_ids": sorted(ids),
    }


def find_receipt(reviews, *, key, head, expected_proxy_login, expected_body, pr_number):
    marker = f"<!-- {MARKER}:{key} -->"
    matches = [item for item in reviews if marker in (item.get("body") or "")]
    if len(matches) > 1:
        raise Stop("unknown", "review_delivery_duplicate_receipts")
    if not matches:
        return None
    item = matches[0]
    if item.get("commit_id") != head:
        raise Stop("stopped", "review_delivery_stale_receipt")
    if (
        item.get("state") != "COMMENTED"
        or item.get("user", {}).get("login") != expected_proxy_login
    ):
        raise Stop("stopped", "review_delivery_provenance_mismatch")
    if item.get("body") != expected_body:
        raise Stop("stopped", "review_delivery_body_mismatch")
    url = item.get("html_url")
    if not isinstance(url, str) or not re.fullmatch(
        rf"https://github\.com/yomote/agent-world/pull/{pr_number}#pullrequestreview-[1-9][0-9]*",
        url,
    ):
        raise Stop("unknown", "review_delivery_receipt_invalid")
    return {
        "url": url,
        "head": head,
        "review_id": item.get("id"),
        "key": key,
        "actor": expected_proxy_login,
        "state": "COMMENTED",
    }


def resolution_query(number):
    return {
        "query": """query($n:Int!){repository(owner:\"yomote\",name:\"agent-world\"){
        pullRequest(number:$n){headRefOid reviewThreads(first:100){nodes{id isResolved isOutdated
        comments(first:20){nodes{body pullRequestReview{databaseId}} pageInfo{hasNextPage}}}
        pageInfo{hasNextPage}}}}}""",
        "variables": {"n": number},
    }


def resolution_mutation(thread_ids):
    if not thread_ids:
        raise Stop("stopped", "review_resolution_empty")
    variables, declarations, fields = {}, [], []
    for index, thread_id in enumerate(thread_ids):
        if not isinstance(thread_id, str) or not thread_id:
            raise Stop("stopped", "review_thread_id_invalid")
        name = f"t{index}"
        variables[name] = thread_id
        declarations.append(f"${name}:ID!")
        fields.append(
            f"r{index}:resolveReviewThread(input:{{threadId:${name}}}){{thread{{id isResolved}}}}"
        )
    return {
        "query": "mutation(" + ",".join(declarations) + "){" + " ".join(fields) + "}",
        "variables": variables,
    }
