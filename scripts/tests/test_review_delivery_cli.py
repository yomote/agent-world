"""Standalone review配送のstage、再起動、unknown停止をnetworkなしで検証する。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))
from scripts.automation.review_delivery import review_key  # noqa: E402
from scripts.automation.review_delivery_cli import StandaloneReviewDelivery  # noqa: E402
from scripts.automation.transport import Stop  # noqa: E402

HEAD = "a" * 40
URL = "https://github.com/yomote/agent-world/pull/93#pullrequestreview-1"


def review():
    return {
        "scope_id": "review-delivery-v1",
        "scope_issue": "#45",
        "scope_definition": "通常PM review配送",
        "required_acceptance_ids": ["R6"],
        "parent_residuals": [
            {"id": "PARENT", "issue": "#45", "owner": "/root/pm", "trigger": "別unit"}
        ],
        "live_postconditions": [{"acceptance_id": "R6", "kind": "github_review_visibility"}],
        "head": HEAD,
        "reviewer": "/root/reviewer",
        "scope": "review delivery",
        "checks": ["owner check", "independent review"],
        "verdict": "pass",
        "findings": [],
        "suppressed": [],
        "acceptance_map": [
            {
                "requirement_id": "REQ",
                "acceptance_id": "R6",
                "issue": "#45",
                "pm_owner": "/root/pm",
                "source": "https://github.com/yomote/agent-world/issues/45#issuecomment-1",
                "source_version": "issuecomment-1",
                "definition": "reviewをUIで確認する",
                "status": "unknown",
                "evidence": "投稿前",
            }
        ],
        "author_review_plan": [
            {
                "id": "PLAN",
                "category": "generic_risk",
                "focus": "unknown write",
                "evidence": "stage tests",
                "known_unmet": "inline live未確認",
            }
        ],
    }


class FakeGitHub:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def call(self, operation, arguments):
        self.calls.append((operation, arguments))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(arguments)


def receipt(number):
    def make(arguments):
        return {
            "url": URL[:-1] + str(number),
            "head": arguments["head"],
            "review_id": number,
            "key": review_key(arguments["review"], arguments["head"]),
            "actor": "yomote",
            "state": "COMMENTED",
        }

    return make


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def final_packet(initial, prior):
    updated = json.loads(json.dumps(initial))
    updated["acceptance_map"][0].update(status="achieved", evidence=f"visible: {prior['url']}")
    return {
        "head": HEAD,
        "pr_number": 93,
        "receipt_url": prior["url"],
        "ui_evidence": {
            "review_url": prior["url"],
            "head": HEAD,
            "pr_number": 93,
            "actor": "yomote",
            "state": "COMMENTED",
            "surfaces": ["conversation", "files_changed"],
        },
        "review_input": updated,
    }


def test_restart_keeps_prior_receipt_and_publishes_exact_final_contract(tmp_path):
    """restartや二段目で旧receiptを捨て、stage gateを迂回する回帰を防ぐ。"""
    source = write(tmp_path / "initial.json", review())
    first = FakeGitHub([receipt(1)])
    delivery = StandaloneReviewDelivery(tmp_path, tmp_path / "state.db", github=first)
    prior = delivery.publish_initial(93, HEAD, source)
    delivery.close()

    second = FakeGitHub([receipt(2)])
    delivery = StandaloneReviewDelivery(tmp_path, tmp_path / "state.db", github=second)
    packet = write(tmp_path / "final.json", final_packet(review(), prior))
    final = delivery.publish_final(93, HEAD, packet)
    row = delivery._row(93, HEAD)
    assert row["state"] == "complete"
    assert json.loads(row["prior_receipt_json"])["url"] == prior["url"]
    assert json.loads(row["final_receipt_json"])["url"] == final["url"]
    sent = second.calls[0][1]["review"]
    assert sent["acceptance_map"][0]["status"] == "achieved"
    assert sent["checks"][-1].startswith("trusted local operator")
    delivery.close()


def test_duplicate_stage_and_changed_contract_are_rejected(tmp_path):
    """同version再投稿やPR作者によるcriteria変更を許す回帰を防ぐ。"""
    source = write(tmp_path / "initial.json", review())
    delivery = StandaloneReviewDelivery(
        tmp_path, tmp_path / "state.db", github=FakeGitHub([receipt(1)])
    )
    prior = delivery.publish_initial(93, HEAD, source)
    with pytest.raises(Stop, match="stage_exists"):
        delivery.publish_initial(93, HEAD, source)
    packet = final_packet(review(), prior)
    packet["review_input"]["scope_definition"] = "作者が縮小"
    with pytest.raises(Stop, match="contract_changed"):
        delivery.publish_final(93, HEAD, write(tmp_path / "bad.json", packet))
    delivery.close()


def test_source_head_and_postcondition_target_evidence_are_bound(tmp_path):
    """別head/PR/receipt/UI観測をcurrent stageの証拠へ流用する回帰を防ぐ。"""
    wrong = review()
    wrong["head"] = "b" * 40
    delivery = StandaloneReviewDelivery(tmp_path, tmp_path / "wrong.db", github=FakeGitHub([]))
    with pytest.raises(Stop, match="head_mismatch"):
        delivery.publish_initial(93, HEAD, write(tmp_path / "wrong.json", wrong))
    delivery.close()

    source = write(tmp_path / "initial.json", review())
    delivery = StandaloneReviewDelivery(
        tmp_path, tmp_path / "state.db", github=FakeGitHub([receipt(1)])
    )
    prior = delivery.publish_initial(93, HEAD, source)
    packet = final_packet(review(), prior)
    packet["ui_evidence"]["head"] = "b" * 40
    with pytest.raises(Stop, match="evidence_invalid"):
        delivery.publish_final(93, HEAD, write(tmp_path / "stale.json", packet))
    packet = final_packet(review(), prior)
    packet["receipt_url"] = packet["receipt_url"] + "-other"
    with pytest.raises(Stop, match="target_mismatch"):
        delivery.publish_final(93, HEAD, write(tmp_path / "other.json", packet))
    delivery.close()


@pytest.mark.parametrize("stage", ["initial", "final"])
def test_unknown_write_requires_explicit_read_only_reconcile(tmp_path, stage):
    """POST結果不明を自動再送せず、exact reviewのread-only照合だけで復帰する。"""
    source = write(tmp_path / "initial.json", review())
    github = FakeGitHub([Stop("unknown", "transport"), receipt(1)])
    delivery = StandaloneReviewDelivery(tmp_path, tmp_path / "state.db", github=github)
    if stage == "initial":
        with pytest.raises(Stop, match="transport"):
            delivery.publish_initial(93, HEAD, source)
        result = delivery.reconcile_initial(93, HEAD)
        assert result["review_id"] == 1
    else:
        delivery.close()
        seed = StandaloneReviewDelivery(
            tmp_path, tmp_path / "state.db", github=FakeGitHub([receipt(1)])
        )
        prior = seed.publish_initial(93, HEAD, source)
        seed.close()
        github = FakeGitHub([Stop("unknown", "transport"), receipt(2)])
        delivery = StandaloneReviewDelivery(tmp_path, tmp_path / "state.db", github=github)
        packet = write(tmp_path / "final.json", final_packet(review(), prior))
        with pytest.raises(Stop, match="transport"):
            delivery.publish_final(93, HEAD, packet)
        result = delivery.reconcile_final(93, HEAD)
        assert result["review_id"] == 2
    assert [call[0] for call in github.calls] == ["publish_pr_review", "reconcile_pr_review"]
    delivery.close()
