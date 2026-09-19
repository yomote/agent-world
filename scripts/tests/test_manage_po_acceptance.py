import json
from datetime import UTC, datetime

import pytest

from scripts.manage_po_acceptance import acknowledge, enqueue, mark_notified, read_store


def package(now: datetime) -> dict:
    return {
        "schema_version": 1,
        "kind": "po-review",
        "request_id": "request-45",
        "objective_ref": "https://github.com/yomote/agent-world/issues/45",
        "value_summary": "継続guardを導入した",
        "artifact_url": "https://github.com/yomote/agent-world/pull/91",
        "preview_url": None,
        "artifact_valid_until": None,
        "expected_actions": ["成果を確認", "acceptまたはchanges requestedを返す"],
        "verified": ["local check pass"],
        "unknowns": ["native push notification"],
        "limits": ["既読は推測しない"],
        "evidence_head": "a" * 40,
        "pr_url": "https://github.com/yomote/agent-world/pull/91",
        "merge_state": "merged",
        "internal_receipt": {
            "schema_version": 1,
            "request_id": "request-45",
            "dod_source_version": "issue-updated:current",
            "requirements_contract_digest": "sha256:" + "c" * 64,
            "evidence_head": "a" * 40,
            "overall": "accept",
        },
        "po_questions": ["期待する価値を満たしたか"],
        "owner": "pm-controller",
        "po_review_required": True,
    }


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def queued(tmp_path):
    package_path = write(tmp_path / "package.json", package(datetime.now(UTC)))
    result = enqueue(tmp_path / "store.json", package_path)
    return tmp_path / "store.json", result["dedup_key"]


def test_package_rejects_missing_review_information(tmp_path):
    """PMが判断するための成果・制約・操作を欠いた通知を防ぐ。"""
    value = package(datetime.now(UTC))
    del value["expected_actions"]

    with pytest.raises(ValueError, match="fields"):
        enqueue(tmp_path / "store.json", write(tmp_path / "package.json", value))


def test_internal_unmet_cannot_enter_completion_review(tmp_path):
    """内部未達をPM向け完成通知へ昇格する回帰を防ぐ。"""
    value = package(datetime.now(UTC))
    value["internal_receipt"]["overall"] = "reject"

    with pytest.raises(ValueError, match="accepted internal closure"):
        enqueue(tmp_path / "store.json", write(tmp_path / "package.json", value))


def test_ready_package_remains_pending_until_pm_ack(tmp_path):
    """outbox保存だけでPM承認済みやcompletedにする回帰を防ぐ。"""
    store, key = queued(tmp_path)

    entry = read_store(store)["entries"][key]

    assert entry["state"] == "ready_for_po_review"
    assert "po_response" not in entry


def test_enqueue_deduplicates_task_head_artifact_and_receipt_version(tmp_path):
    """同じ成果をFront Desk outboxへ重複投入する回帰を防ぐ。"""
    value = package(datetime.now(UTC))
    path = write(tmp_path / "package.json", value)
    first = enqueue(tmp_path / "store.json", path)
    second = enqueue(tmp_path / "store.json", path)

    assert first["dedup_key"] == second["dedup_key"]
    assert second["changed"] is False


def test_unknown_notification_is_frozen_without_retry(tmp_path):
    """送信結果不明を自動再送して二重通知する回帰を防ぐ。"""
    store, key = queued(tmp_path)
    receipt = {
        "channel": "front-desk-same-thread",
        "status": "unknown",
        "observed_at": datetime.now(UTC).isoformat(),
        "message_ref": None,
    }
    result = mark_notified(store, key, write(tmp_path / "notify.json", receipt))

    assert result["state"] == "notification_unknown"
    with pytest.raises(ValueError, match="not ready"):
        mark_notified(store, key, tmp_path / "notify.json")


def test_restart_recovers_saved_outbox_before_notification(tmp_path):
    """process再開時にpending packageを失って再生成する回帰を防ぐ。"""
    store, key = queued(tmp_path)
    assert read_store(store)["entries"][key]["state"] == "ready_for_po_review"
    receipt = {
        "channel": "front-desk-same-thread",
        "status": "accepted",
        "observed_at": datetime.now(UTC).isoformat(),
        "message_ref": "front-desk-message-1",
    }

    result = mark_notified(store, key, write(tmp_path / "notify.json", receipt))

    assert result == {"dedup_key": key, "state": "notified", "human_read_verified": False}


def test_changes_requested_returns_to_owner_with_trigger(tmp_path):
    """PM差戻しを承認済みにせず次owner・action・復帰条件を保持する。"""
    store, key = queued(tmp_path)
    notified = {
        "channel": "front-desk-same-thread",
        "status": "accepted",
        "observed_at": datetime.now(UTC).isoformat(),
        "message_ref": "front-desk-message-1",
    }
    mark_notified(store, key, write(tmp_path / "notify.json", notified))
    response = {
        "decision": "changes_requested",
        "acknowledged_at": datetime.now(UTC).isoformat(),
        "channel": "front-desk-same-thread",
        "message_ref": "pm-message-1",
        "owner": "implementation-owner",
        "next_action": "指摘を反映する",
        "resume_trigger": "修正版headの独立closure audit完了時",
    }

    result = acknowledge(store, key, write(tmp_path / "response.json", response))

    assert result["state"] == "changes_requested"
    assert result["po_acceptance_receipt"] is None
    assert read_store(store)["entries"][key]["po_response"]["owner"] == "implementation-owner"


def test_accepted_ack_emits_receipt_bound_to_internal_closure(tmp_path):
    """PO acceptanceを通知receiptと分け、同じrequest・head・DoD版へ固定する。"""
    store, key = queued(tmp_path)
    notified = {
        "channel": "front-desk-same-thread",
        "status": "accepted",
        "observed_at": datetime.now(UTC).isoformat(),
        "message_ref": "front-desk-message-1",
    }
    mark_notified(store, key, write(tmp_path / "notify.json", notified))
    response = {
        "decision": "accepted",
        "acknowledged_at": datetime.now(UTC).isoformat(),
        "channel": "front-desk-same-thread",
        "message_ref": "pm-message-1",
        "owner": None,
        "next_action": None,
        "resume_trigger": None,
    }

    result = acknowledge(store, key, write(tmp_path / "response.json", response))

    receipt = result["po_acceptance_receipt"]
    assert receipt["dedup_key"] == key
    assert receipt["evidence_head"] == "a" * 40
    assert receipt["dod_source_version"] == "issue-updated:current"
    assert receipt["requirements_contract_digest"] == "sha256:" + "c" * 64
