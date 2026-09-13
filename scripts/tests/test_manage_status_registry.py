import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ops_status.models import StatusSnapshot

from scripts.manage_status_registry import boot, claim, digest_bundle, prepare, read_registry
from scripts.sync_status_from_local_events import snapshot_payload


def registry(at: datetime) -> dict:
    return {
        "generation": 4,
        "active_front_desk": {"alias": "front-desk-1", "claimed_at": at.isoformat()},
        "updated_at": at.isoformat(),
        "source": "manual-public-registry",
        "requests": [
            {
                "request_id": "request-64",
                "scope_id": "scope-request-64",
                "issue_url": "https://github.com/yomote/agent-world/issues/64",
                "issue_state": "open",
                "issue_observation": "confirmed",
                "issue_observed_at": at.isoformat(),
                "public_title": "依頼registry",
                "public_purpose": "新しい窓口へ引き継ぐ",
                "acceptance_summary": "二重claimを防ぐ",
                "authority_source": "github-issue-observation",
                "lifecycle": "handover-waiting",
                "owner_agent": "status-owner",
                "member_agents": ["status-owner"],
                "progress_summary": "bundle準備済み",
                "next_action": "新しい窓口がclaim",
                "report_updated_at": at.isoformat(),
                "report_source": "manual-public-summary",
                "runtime_connection": "record-only",
                "runtime_observed_at": at.isoformat(),
                "evidence": [],
            }
        ],
    }


def test_prepare_is_canonical_and_claim_verifies_digest(tmp_path):
    """環境やJSON整形差で引継digestが変わる回帰を防ぐ。"""
    at = datetime.now(UTC)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry(at)), encoding="utf-8")
    artifact = prepare(registry_path, "front-desk-2", at.isoformat())
    assert artifact["bundle_digest"] == digest_bundle(artifact["bundle"])
    artifact_path = tmp_path / "bundle.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    update = claim(artifact_path, "front-desk-2", at.isoformat(), "runtime-new")

    assert update["expected_generation"] == 5
    assert update["bundle_digest"] == artifact["bundle_digest"]
    assert update["actor_runtime_session_id"] == "runtime-new"
    restored = boot(artifact_path)
    assert restored["claim_required"] is True
    assert restored["workers_started"] is False
    assert restored["requests"][0]["request_id"] == "request-64"
    assert read_registry(registry_path)["active_front_desk"]["alias"] == "front-desk-1"


def test_claim_rejects_tampered_or_wrong_successor(tmp_path):
    """改変bundleや別窓口によるclaimを防ぐ。"""
    at = datetime.now(UTC)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry(at)), encoding="utf-8")
    artifact = prepare(registry_path, "front-desk-2", at.isoformat())
    artifact_path = tmp_path / "bundle.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="different successor"):
        claim(artifact_path, "front-desk-3", at.isoformat())
    artifact["bundle"]["requests"][0]["next_action"] = "改変"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        claim(artifact_path, "front-desk-2", at.isoformat())


def test_compat_v1_omits_request_registry():
    """旧status APIへ新registry fieldを誤送信する回帰を防ぐ。"""
    fixture = Path(__file__).parent / "fixtures" / "request_registry_status.json"
    snapshot = StatusSnapshot.model_validate_json(fixture.read_text(encoding="utf-8"))

    assert "request_registry" not in snapshot_payload(snapshot, compat_v1=True)
