"""通常PR review配送の型・位置・冪等境界をネットワークなしで検証する。"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))
from scripts.automation.github_adapter import GitHub  # noqa: E402
from scripts.automation.review_delivery import (  # noqa: E402
    acceptance_gate,
    find_receipt,
    issue_close_gate,
    prepare,
    render_review_input,
    resolution_mutation,
    review_key,
)
from scripts.automation.transport import Stop  # noqa: E402

HEAD = "a" * 40
FILES = [
    {
        "filename": "apps/example.py",
        "patch": "@@ -8,3 +8,4 @@\n context\n-old\n+new\n+added\n context2",
    }
]


def review(**updates):
    finding = {
        "id": "F-1",
        "requirement_id": "REQ-9",
        "acceptance_id": "ACC-2",
        "path": "apps/example.py",
        "line": 10,
        "side": "RIGHT",
        "severity": "P1",
        "category": "business_invariant",
        "problem": "tenant境界を越える",
        "failure_scenario": "別tenantの行が候補へ入る",
        "impact": "誤ったレビュー資料になる",
        "evidence": "mixed tenant fixtureで再現",
        "request": "tenantでfilterする",
        "blocking_rationale": "業務成果の正しさを失うため",
    }
    value = {
        "scope_id": "accounting-package-slice-v1",
        "scope_issue": "#90",
        "scope_definition": "read-only review packageの最小縦切り",
        "required_acceptance_ids": ["ACC-2"],
        "parent_residuals": [
            {
                "id": "ISSUE-89-COMBINED",
                "issue": "#89",
                "owner": "/root/pm",
                "trigger": "別unitで再開",
            }
        ],
        "head": HEAD,
        "reviewer": "/root/reviewer",
        "scope": "会計read-only package",
        "checks": ["semantic review", "domain property tests"],
        "verdict": "fail",
        "findings": [finding],
        "suppressed": [{"id": "IDEA-1", "reason": "backlog", "explanation": "今回scope外の改善"}],
        "acceptance_map": [
            {
                "requirement_id": "REQ-9",
                "acceptance_id": "ACC-2",
                "issue": "#90",
                "pm_owner": "/root/pm_controller",
                "source": "https://github.com/yomote/agent-world/issues/89",
                "source_version": "issue-comment-1",
                "definition": "mixed tenantを候補へ含めない",
                "status": "unmet",
                "evidence": "mixed tenant fixtureの検証待ち",
            }
        ],
        "author_review_plan": [
            {
                "id": "PLAN-1",
                "category": "business_invariant",
                "focus": "tenant境界",
                "evidence": "mixed tenant fixture",
                "known_unmet": "実GitHub inline配送は未確認",
            }
        ],
    }
    value.update(updates)
    return value


def test_finding_becomes_comment_review_with_requirement_and_provenance():
    """汎用riskだけの曖昧な指摘やself-authの偽APPROVEに戻る回帰を防ぐ。"""
    result = prepare(review(), head=HEAD, files=FILES, proxy_login="yomote", pr_author="yomote")
    assert result["payload"]["event"] == "COMMENT"
    assert result["payload"]["commit_id"] == HEAD
    assert result["payload"]["comments"][0]["line"] == 10
    assert "REQ-9" in result["payload"]["comments"][0]["body"]
    assert "代行記録" in result["payload"]["body"]
    assert "APPROVE/CHANGES_REQUESTED" in result["payload"]["body"]
    public_input = render_review_input(review())
    assert "REQ-9 / ACC-2" in public_input
    assert "issue-comment-1" in public_input
    assert "実GitHub inline配送は未確認" in public_input


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"head": "b" * 40}, "head_mismatch"),
        ({"event": "APPROVE"}, "must_be_comment"),
        ({"findings": [{**review()["findings"][0], "side": "MIDDLE"}]}, "location"),
        ({"findings": [{**review()["findings"][0], "line": 99}]}, "location"),
        ({"findings": [{**review()["findings"][0], "path": "other.py"}]}, "location"),
    ],
)
def test_stale_head_bad_location_and_self_approval_stop_before_post(change, reason):
    """古いhead・diff外line・不正side・自己承認をGitHubへ送る回帰を防ぐ。"""
    with pytest.raises(Stop, match=reason):
        prepare(review(**change), head=HEAD, files=FILES, proxy_login="yomote", pr_author="yomote")


def test_zero_findings_has_summary_and_no_inline_comment():
    """指摘0件で架空inlineを作る、またはscope/check/headを落とす回帰を防ぐ。"""
    result = prepare(
        review(verdict="pass", findings=[]),
        head=HEAD,
        files=FILES,
        proxy_login="delivery-owner",
        pr_author="author",
    )
    assert result["payload"]["comments"] == []
    assert "findings: 0" in result["payload"]["body"]
    assert HEAD in result["payload"]["body"]
    assert "semantic review" in result["payload"]["body"]


def test_required_acceptance_must_be_achieved_before_ready():
    """親Issue残件とPR必須ACを混同し、unknownの作者申告だけでReadyにする回帰を防ぐ。"""
    assert acceptance_gate(review())["ready"] is False
    achieved = review()
    achieved["acceptance_map"][0]["status"] = "achieved"
    assert acceptance_gate(achieved) == {
        "scope_id": "accounting-package-slice-v1",
        "scope_issue": "#90",
        "required": ["ACC-2"],
        "unmet": [],
        "ready": True,
    }


def test_exact_remote_receipt_is_reused_but_stale_or_duplicate_stops():
    """結果不明後に同じreviewを再投稿したり古いreceiptを付替える回帰を防ぐ。"""
    prepared = prepare(review(), head=HEAD, files=FILES, proxy_login="owner", pr_author="owner")
    item = {
        "id": 7,
        "commit_id": HEAD,
        "html_url": "https://github.com/yomote/agent-world/pull/9#pullrequestreview-7",
        "body": prepared["payload"]["body"],
        "state": "COMMENTED",
        "user": {"login": "owner"},
    }
    assert (
        find_receipt(
            [item],
            key=prepared["key"],
            head=HEAD,
            expected_proxy_login="owner",
            expected_body=item["body"],
            pr_number=9,
        )["review_id"]
        == 7
    )
    with pytest.raises(Stop, match="stale"):
        find_receipt(
            [{**item, "commit_id": "b" * 40}],
            key=prepared["key"],
            head=HEAD,
            expected_proxy_login="owner",
            expected_body=item["body"],
            pr_number=9,
        )
    with pytest.raises(Stop, match="duplicate"):
        find_receipt(
            [item, {**item, "id": 8}],
            key=prepared["key"],
            head=HEAD,
            expected_proxy_login="owner",
            expected_body=item["body"],
            pr_number=9,
        )
    with pytest.raises(Stop, match="provenance"):
        find_receipt(
            [item],
            key=prepared["key"],
            head=HEAD,
            expected_proxy_login="other",
            expected_body=item["body"],
            pr_number=9,
        )
    with pytest.raises(Stop, match="body_mismatch"):
        find_receipt(
            [{**item, "body": item["body"] + " altered"}],
            key=prepared["key"],
            head=HEAD,
            expected_proxy_login="owner",
            expected_body=item["body"],
            pr_number=9,
        )
    with pytest.raises(Stop, match="receipt_invalid"):
        find_receipt(
            [{**item, "html_url": "https://github.com/other/repo/pull/9#pullrequestreview-7"}],
            key=prepared["key"],
            head=HEAD,
            expected_proxy_login="owner",
            expected_body=item["body"],
            pr_number=9,
        )


def test_acceptance_ids_are_unique_and_full_contract_changes_dedup_key():
    """重複ACの後勝ちやscope/residual差を同じreview keyへ潰す回帰を防ぐ。"""
    value = review()
    duplicate = review()
    duplicate["acceptance_map"].append({**duplicate["acceptance_map"][0]})
    with pytest.raises(Stop, match="acceptance_id_duplicate"):
        prepare(duplicate, head=HEAD, files=FILES, proxy_login="owner", pr_author="owner")
    changed = review()
    changed["scope_definition"] = "別の下位scope"
    assert review_key(value, HEAD) != review_key(changed, HEAD)


def test_issue_close_is_bound_to_scope_and_parent_residuals():
    """部分PRのmergeが親Issueを完了扱いして閉じる回帰を防ぐ。"""
    value = review()
    assert issue_close_gate(value, 90)["may_close"] is True
    assert issue_close_gate(value, 89) == {
        "may_close": False,
        "reason": "issue_not_completed_by_pr_scope",
        "scope_issue": "#90",
        "parent_residual_issues": [89],
    }


def test_resolution_is_one_graphql_write_for_exact_original_threads():
    """複数threadの部分成功を個別retryし、別lineへ付替える回帰を防ぐ。"""
    payload = resolution_mutation(["THREAD-1", "THREAD-2"])
    assert payload["variables"] == {"t0": "THREAD-1", "t1": "THREAD-2"}
    assert payload["query"].count("resolveReviewThread") == 2


class Task:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.name = "test-campaign"


def github_with_request(send):
    task = Task()
    github = GitHub.__new__(GitHub)
    github.task = task
    task.db.executescript("""
        CREATE TABLE delivery_review_receipts(
            campaign TEXT,pr INTEGER,head TEXT,delivery_key TEXT,url TEXT,review_id INTEGER,
            PRIMARY KEY(campaign,pr,head));
    """)
    github.request = send
    return github


def test_adapter_deduplicates_exact_review_and_keeps_visible_receipt():
    """remote receiptがあるのに同じreviewを再投稿してnotificationを重複させる回帰を防ぐ。"""
    posted, remote = [], []

    def send(operation, method, path, payload=None):
        if operation == "review_pr":
            return {"head": {"sha": HEAD}, "user": {"login": "yomote"}}
        if operation == "review_files":
            return FILES
        if operation == "review_actor":
            return {"login": "yomote"}
        if operation == "review_list":
            return list(remote)
        assert operation == "publish_pr_review"
        posted.append(payload)
        result = {
            "id": 81,
            "commit_id": HEAD,
            "html_url": "https://github.com/yomote/agent-world/pull/9#pullrequestreview-81",
            "body": payload["body"],
            "state": "COMMENTED",
            "user": {"login": "yomote"},
        }
        remote.append(result)
        return result

    github = github_with_request(send)
    args = {"head": HEAD, "review": review()}
    first = github.review_delivery(9, args, publish=True)
    second = github.review_delivery(9, args, publish=True)
    assert first == second
    assert first["head"] == HEAD and first["inline_count"] == 1
    assert len(posted) == 1 and posted[0]["event"] == "COMMENT"


def test_adapter_rechecks_head_before_and_after_review_post():
    """files取得後やPOST応答後のhead変更をcurrent receiptとして保存する回帰を防ぐ。"""
    reads = []

    def send(operation, method, path, payload=None):
        if operation == "review_pr":
            reads.append(1)
            current = HEAD if len(reads) < 3 else "b" * 40
            return {"head": {"sha": current}, "user": {"login": "owner"}}
        if operation == "review_files":
            return FILES
        if operation == "review_actor":
            return {"login": "owner"}
        if operation == "review_list":
            return []
        pytest.fail("POST reached after stale head")

    github = github_with_request(send)
    with pytest.raises(Stop, match="head_mismatch"):
        github.review_delivery(9, {"head": HEAD, "review": review()}, publish=True)
    assert len(reads) == 3


def test_adapter_never_retries_unknown_review_write():
    """結果不明のPOSTを同じcall内でlookup・再送して二重reviewにする回帰を防ぐ。"""
    writes = []

    def send(operation, method, path, payload=None):
        if operation == "review_pr":
            return {"head": {"sha": HEAD}, "user": {"login": "author"}}
        if operation == "review_files":
            return FILES
        if operation == "review_actor":
            return {"login": "proxy"}
        if operation == "review_list":
            return []
        writes.append(payload)
        raise Stop("unknown", "github_transport_no_retry")

    github = github_with_request(send)
    with pytest.raises(Stop, match="no_retry"):
        github.review_delivery(9, {"head": HEAD, "review": review()}, publish=True)
    assert len(writes) == 1


def test_adapter_resolves_only_original_thread_after_new_head_recheck():
    """修正後reviewなしで解決したり、outdated findingを別lineへ付け替える回帰を防ぐ。"""
    calls = []

    def send(operation, method, path, payload=None):
        calls.append((operation, payload))
        if operation == "review_threads":
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "headRefOid": HEAD,
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "id": "THREAD-1",
                                        "isResolved": False,
                                        "isOutdated": True,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "body": (
                                                        "<!-- agent-world-review-delivery-"
                                                        "finding:F-1 -->"
                                                    ),
                                                    "pullRequestReview": {"databaseId": 81},
                                                }
                                            ],
                                            "pageInfo": {"hasNextPage": False},
                                        },
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False},
                            },
                        }
                    }
                }
            }
        assert operation == "resolve_pr_review_threads"
        return {"data": {"r0": {"thread": {"id": "THREAD-1", "isResolved": True}}}}

    github = github_with_request(send)
    result = github.resolve_review_threads(
        9,
        {
            "head": HEAD,
            "review_id": 81,
            "finding_ids": ["F-1"],
            "recheck": {"head": HEAD, "verdict": "pass", "findings": []},
        },
    )
    assert result == {"head": HEAD, "resolved": ["F-1"], "already_resolved": False}
    assert calls[1][1]["variables"] == {"t0": "THREAD-1"}


def test_adapter_rejects_resolution_without_current_head_recheck_before_api():
    """old headのreviewやfinding残存中にthreadを解決する回帰を防ぐ。"""
    github = github_with_request(lambda *args, **kwargs: pytest.fail("API reached"))
    with pytest.raises(Stop, match="recheck_invalid"):
        github.resolve_review_threads(
            9,
            {
                "head": HEAD,
                "review_id": 81,
                "finding_ids": ["F-1"],
                "recheck": {"head": "b" * 40, "verdict": "pass", "findings": []},
            },
        )
