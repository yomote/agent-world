import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ops_status.models import StatusSnapshot

from scripts.manage_status_registry import (
    boot,
    claim,
    closure_check,
    continuity_errors,
    digest_bundle,
    discover,
    mark_claimed,
    prepare,
    read_registry,
    root_runtime_session_id,
)
from scripts.sync_status_from_local_events import (
    runtime_binding_from_claim_marker,
    snapshot_payload,
)


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
                "resume_trigger": "成功claim receiptを確認後に明示dispatch",
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


def test_root_runtime_binding_uses_thread_id_and_rejects_child_or_missing_provenance():
    """child自身のsession IDをactive Front Deskとしてclaimする回帰を防ぐ。"""
    root = "11111111-1111-4111-8111-111111111111"
    child = "22222222-2222-4222-8222-222222222222"
    assert (
        root_runtime_session_id(
            {"CODEX_THREAD_ID": root, "CODEX_SESSION_ID": child},
            "/root/pm/front_desk_sync",
        )
        == root
    )
    with pytest.raises(ValueError, match="distinct session ID"):
        root_runtime_session_id(
            {"CODEX_THREAD_ID": root, "CODEX_SESSION_ID": root}, "/root/pm/worker"
        )
    with pytest.raises(ValueError, match="top-level root runtime"):
        root_runtime_session_id(
            {"CODEX_THREAD_ID": "not-a-uuid", "CODEX_SESSION_ID": child},
            "/root/pm/worker",
        )
    with pytest.raises(ValueError, match="canonical task path"):
        root_runtime_session_id({"CODEX_THREAD_ID": root, "CODEX_SESSION_ID": child}, "/root")


def test_cli_rejects_raw_runtime_claim_path():
    """runtime provenance検査を生のCLI引数で迂回する回帰を防ぐ。"""
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "manage_status_registry.py"),
            "claim",
            "--runtime-session-id",
            "unverified-runtime",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "claim-root" in result.stderr


def test_claim_rejects_tampered_or_wrong_successor(tmp_path):
    """改変bundleや別窓口によるclaimを防ぐ。"""
    at = datetime.now(UTC)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry(at)), encoding="utf-8")
    artifact = prepare(registry_path, "front-desk-2", at.isoformat())
    artifact_path = tmp_path / "bundle.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="different successor"):
        claim(artifact_path, "front-desk-3", at.isoformat(), "runtime-new")
    artifact["bundle"]["requests"][0]["next_action"] = "改変"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        claim(artifact_path, "front-desk-2", at.isoformat(), "runtime-new")

    artifact = prepare(registry_path, "front-desk-2", at.isoformat())
    artifact["update"]["expected_generation"] = 99
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="prepare update"):
        boot(artifact_path)


def test_compat_v1_omits_request_registry():
    """旧status APIへ新registry fieldを誤送信する回帰を防ぐ。"""
    fixture = Path(__file__).parent / "fixtures" / "request_registry_status.json"
    snapshot = StatusSnapshot.model_validate_json(fixture.read_text(encoding="utf-8"))

    assert "request_registry" not in snapshot_payload(snapshot, compat_v1=True)


def locator(tmp_path: Path, artifact_path: Path, artifact: dict) -> Path:
    root = tmp_path / "repo"
    (root / "docs/runbooks").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "docs/runbooks/request-registry.md").write_text("runbook", encoding="utf-8")
    (root / "scripts/manage_status_registry.py").write_text("cli", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    start = tmp_path / "start.md"
    start.write_text("start", encoding="utf-8")
    context = {
        "schema_version": 1,
        "source": "final-registry-snapshot-and-prepare-receipt",
        "bundle_digest": artifact["bundle_digest"],
        "registry_generation_before_prepare": artifact["bundle"]["expected_generation"],
        "registry_generation_after_prepare": artifact["bundle"]["expected_generation"] + 1,
        "handover_state": "ready",
        "from_front_desk": artifact["bundle"]["from_front_desk"],
        "to_front_desk": artifact["bundle"]["to_front_desk"],
        "prepared_at": artifact["bundle"]["prepared_at"],
        "resume_policy": "explicit-dispatch-required",
        "requests": [
            {
                **request,
                "public_title": "公開依頼",
                "public_purpose": "新しい窓口へ目的を復元する",
                "acceptance_summary": "claim前に引継内容を確認する",
                "issue_state": "open",
                "issue_observation": "confirmed",
                "issue_observed_at": artifact["bundle"]["prepared_at"],
                "authority_source": "github-issue-observation",
                "owner_agent": "status-owner",
                "member_agents": ["status-owner"],
                "progress_summary": "handover ready",
                "blocker": None,
                "report_source": "manual-public-summary",
                "runtime_connection": "record-only",
                "runtime_observed_at": artifact["bundle"]["prepared_at"],
                "evidence": [],
                "worker_restart_policy": "no-automatic-restart",
            }
            for request in artifact["bundle"]["requests"]
        ],
        "dispatch_policy": "successful-claim-receipt-then-explicit-dispatch-only",
    }
    context_path = tmp_path / "public-context.json"
    context_path.write_text(
        json.dumps({"context": context, "context_digest": digest_bundle(context)}),
        encoding="utf-8",
    )
    (root / ".codex").mkdir()
    path = root / ".codex/handoff-locator.local.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "yomote/agent-world",
                "repo_url": "https://github.com/yomote/agent-world",
                "registry_source_commit": source_commit,
                "runbook": "docs/runbooks/request-registry.md",
                "cli": "scripts/manage_status_registry.py",
                "bundle_path": str(artifact_path.resolve()),
                "start_path": str(start.resolve()),
                "context_path": str(context_path.resolve()),
                "context_digest": digest_bundle(context),
                "expected_generation": artifact["bundle"]["expected_generation"] + 1,
                "bundle_digest": artifact["bundle_digest"],
                "from_front_desk": artifact["bundle"]["from_front_desk"],
                "to_front_desk": artifact["bundle"]["to_front_desk"],
                "claim_executed": False,
                "claim_policy": "new-top-level-root-explicit-claim-only",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_discover_verifies_project_locator_and_frozen_bundle(tmp_path):
    """別project、改変bundle、古いgenerationから自動claimする回帰を防ぐ。"""
    at = datetime.now(UTC)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry(at)), encoding="utf-8")
    artifact = prepare(registry_path, "front-desk-2", at.isoformat())
    artifact_path = tmp_path / "bundle.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    locator_path = locator(tmp_path, artifact_path, artifact)

    result = discover(locator_path, tmp_path / "repo")

    assert result["local_handover_candidate"] is True
    assert result["expected_generation"] == 5
    assert result["claim_required"] is True
    assert result["workers_started"] is False
    assert result["server_cas_still_required"] is True
    assert result["request_context"][0]["public_purpose"] == "新しい窓口へ目的を復元する"

    saved = json.loads(locator_path.read_text(encoding="utf-8"))
    saved["expected_generation"] = 6
    locator_path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen bundle"):
        discover(locator_path, tmp_path / "repo")


def test_discover_missing_or_claimed_locator_never_starts_workers(tmp_path):
    """readyなしや旧root/childからの二重claim・自動再起動を防ぐ。"""
    root = tmp_path / "missing-repo"
    root.mkdir()
    missing = discover(root / ".codex/handoff-locator.local.json", root)
    assert missing == {
        "local_handover_candidate": False,
        "reason": "locator-not-found",
        "claim_required": False,
        "workers_started": False,
    }

    at = datetime.now(UTC)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry(at)), encoding="utf-8")
    artifact = prepare(registry_path, "front-desk-2", at.isoformat())
    artifact_path = tmp_path / "bundle.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    locator_path = locator(tmp_path, artifact_path, artifact)
    saved = json.loads(locator_path.read_text(encoding="utf-8"))
    saved["claim_executed"] = True
    locator_path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="already claimed"):
        discover(locator_path, tmp_path / "repo")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("extra", "allowlist"),
        ("scope", "values do not match"),
        ("evidence", "HTTPS URL"),
    ],
)
def test_discover_rejects_public_context_tampering(tmp_path, change, message):
    """digestを再計算した未知fieldや正本不一致も復元結果へ混ぜる回帰を防ぐ。"""
    at = datetime.now(UTC)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry(at)), encoding="utf-8")
    artifact = prepare(registry_path, "front-desk-2", at.isoformat())
    artifact_path = tmp_path / "bundle.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    locator_path = locator(tmp_path, artifact_path, artifact)
    saved_locator = json.loads(locator_path.read_text(encoding="utf-8"))
    context_path = Path(saved_locator["context_path"])
    context_artifact = json.loads(context_path.read_text(encoding="utf-8"))
    request = context_artifact["context"]["requests"][0]
    if change == "extra":
        request["conversation"] = "must not pass"
    elif change == "scope":
        request["scope_id"] = "different-scope"
    else:
        request["evidence"] = [
            {"kind": "issue", "url": "http://invalid.example", "observed_at": at.isoformat()}
        ]
    changed_digest = digest_bundle(context_artifact["context"])
    context_artifact["context_digest"] = changed_digest
    context_path.write_text(json.dumps(context_artifact), encoding="utf-8")
    saved_locator["context_digest"] = changed_digest
    locator_path.write_text(json.dumps(saved_locator), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        discover(locator_path, tmp_path / "repo")


def test_mark_claimed_requires_success_receipt_before_disabling_locator(tmp_path):
    """結果不明や不一致receiptでlocal ready markerを消す回帰を防ぐ。"""
    at = datetime.now(UTC)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry(at)), encoding="utf-8")
    artifact = prepare(registry_path, "front-desk-2", at.isoformat())
    artifact_path = tmp_path / "bundle.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    locator_path = locator(tmp_path, artifact_path, artifact)
    claim_path = tmp_path / "claim.json"
    claim_path.write_text(
        json.dumps(claim(artifact_path, "front-desk-2", at.isoformat(), "runtime-new")),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "receipt.json"
    receipt = {
        "changed": True,
        "generation": 6,
        "active_front_desk": {
            "alias": "front-desk-2",
            "runtime_session_id": "runtime-new",
        },
        "handover": {
            "state": "accepted",
            "to_front_desk": "front-desk-2",
            "bundle_digest": artifact["bundle_digest"],
        },
    }
    receipt_path.write_text(json.dumps({**receipt, "generation": 5}), encoding="utf-8")
    with pytest.raises(ValueError, match="does not prove"):
        mark_claimed(locator_path, claim_path, receipt_path, tmp_path / "repo")
    assert json.loads(locator_path.read_text(encoding="utf-8"))["claim_executed"] is False

    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = mark_claimed(locator_path, claim_path, receipt_path, tmp_path / "repo")

    assert result == {
        "claim_marked": True,
        "generation": 6,
        "active_front_desk": "front-desk-2",
        "runtime_binding": {
            "registry_generation": 6,
            "front_desk_alias": "front-desk-2",
            "runtime_session_digest": "sha256:" + hashlib.sha256(b"runtime-new").hexdigest(),
        },
        "workers_started": False,
    }
    marker = json.loads(
        (tmp_path / "repo/.codex/handoff-claim.local.json").read_text(encoding="utf-8")
    )
    assert marker["runtime_session_id"] == "runtime-new"
    assert (
        runtime_binding_from_claim_marker(tmp_path / "repo/.codex/handoff-claim.local.json")
        == result["runtime_binding"]
    )
    with pytest.raises(ValueError, match="successful claim marker"):
        discover(locator_path, tmp_path / "repo")


def closure_payload(at: datetime) -> dict:
    gate = {
        "state": "achieved",
        "evidence_refs": ["https://github.com/yomote/agent-world/pull/91"],
        "reason": None,
    }
    return {
        "schema_version": 1,
        "request": {
            "request_id": "request-45",
            "objective_ref": "https://github.com/yomote/agent-world/issues/45",
            "latest_dod_version": "issue-updated:2026-09-19T03:13:59Z",
            "required_requirement_ids": ["generic-exact-head", "domain-use-case"],
            "requirements_contract_digest": "sha256:" + "c" * 64,
            "expected_head": "a" * 40,
            "issue_state": "closed",
            "terminal_intent": "complete",
        },
        "audit": {
            "schema_version": 1,
            "request_id": "request-45",
            "objective_ref": "https://github.com/yomote/agent-world/issues/45",
            "dod_source_version": "issue-updated:2026-09-19T03:13:59Z",
            "requirements_contract_digest": "sha256:" + "c" * 64,
            "evidence_head": "a" * 40,
            "checker_agent": "independent-checker",
            "checked_at": at.isoformat(),
            **{
                name: gate.copy()
                for name in ("code_done", "verified", "reviewed", "merged", "delivered", "purpose")
            },
            "requirements": [
                {
                    "requirement_id": "generic-exact-head",
                    "category": "generic-quality",
                    "source_version": "quality-contract:v1",
                    "verification_method": "current-head check",
                    "evidence_refs": ["https://github.com/yomote/agent-world/pull/91/checks"],
                    "evidence_head": "a" * 40,
                    "checker_agent": "independent-checker",
                    "result": "satisfied",
                    "reason": None,
                },
                {
                    "requirement_id": "domain-use-case",
                    "category": "domain",
                    "source_version": "issue-updated:2026-09-19T03:13:59Z",
                    "verification_method": "use-case acceptance",
                    "evidence_refs": ["https://github.com/yomote/agent-world/issues/45"],
                    "evidence_head": "a" * 40,
                    "checker_agent": "independent-checker",
                    "result": "satisfied",
                    "reason": None,
                },
            ],
            "overall": "accept",
            "owner": None,
            "next_action": None,
            "resume_trigger": None,
            "stop_decision": {"made": False, "reason": None},
        },
    }


def run_closure(tmp_path: Path, payload: dict) -> dict:
    path = tmp_path / "closure.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return closure_check(path)


@pytest.mark.parametrize(
    "case",
    [
        "success-receipt-wrong-dod",
        "skipped-tests",
        "stale-head",
        "unresolved-review",
        "merged-business-failed",
        "ci-pass-domain-wrong",
        "missing-required-id",
        "blocked-without-owner",
        "cancelled",
        "continue-intent",
        "stop-decision",
        "required-not-applicable",
    ],
)
def test_closure_check_rejects_false_completion_signals(tmp_path, case):
    """技術的terminalや中止を目的達成へ昇格する回帰を防ぐ。"""
    payload = closure_payload(datetime.now(UTC))
    audit = payload["audit"]
    if case == "success-receipt-wrong-dod":
        audit["dod_source_version"] = "issue-updated:old"
    elif case == "skipped-tests":
        audit["verified"] = {"state": "unmet", "evidence_refs": [], "reason": "tests skipped"}
    elif case == "stale-head":
        audit["evidence_head"] = "b" * 40
    elif case == "unresolved-review":
        audit["reviewed"] = {
            "state": "unknown",
            "evidence_refs": [],
            "reason": "review unresolved",
        }
    elif case == "merged-business-failed":
        audit["purpose"] = {
            "state": "unmet",
            "evidence_refs": ["https://github.com/yomote/agent-world/issues/45"],
            "reason": "business DoD failed",
        }
    elif case == "ci-pass-domain-wrong":
        audit["requirements"][1].update(
            result="unmet", evidence_refs=[], reason="domain output violates the use case"
        )
    elif case == "missing-required-id":
        audit["requirements"].pop()
    elif case == "blocked-without-owner":
        audit["merged"] = {
            "state": "unmet",
            "evidence_refs": [],
            "reason": "merge gate blocked",
        }
    elif case == "cancelled":
        payload["request"]["terminal_intent"] = "cancelled"
        audit["stop_decision"] = {"made": True, "reason": "hypothesis stopped"}
    elif case == "continue-intent":
        payload["request"]["terminal_intent"] = "continue"
    elif case == "stop-decision":
        audit["stop_decision"] = {"made": True, "reason": "hypothesis stopped"}
    else:
        audit["requirements"][0].update(
            result="not_applicable", evidence_refs=[], reason="checker chose to skip it"
        )
    if case in {
        "skipped-tests",
        "unresolved-review",
        "merged-business-failed",
        "ci-pass-domain-wrong",
    }:
        audit["overall"] = "unknown" if case == "unresolved-review" else "reject"
    if case != "blocked-without-owner":
        audit.update(
            {
                "owner": "pm-controller",
                "next_action": "PMが次のbounded packetを分配",
                "resume_trigger": "既承認scope内の新packet採用時",
            }
        )

    result = run_closure(tmp_path, payload)

    assert result["closure_status"] != "accept"
    assert result["receipt"] is None
    if case == "blocked-without-owner":
        assert "unaccepted closure needs owner" in result["reasons"]


def test_closure_check_accepts_only_current_fully_evidenced_objective(tmp_path):
    """最新DoD・exact head・全gateの証跡が揃った目的だけを完了receiptにする。"""
    payload = closure_payload(datetime.now(UTC))

    result = run_closure(tmp_path, payload)

    assert result["closure_status"] == "accept"
    assert result["receipt"]["overall"] == "accept"
    assert result["continuation"] is None
    assert result["purpose_achieved"] == "achieved"


def test_issue_89_audit_rejects_done_and_returns_continuity(tmp_path):
    """Issue 89の技術成果や中止判断を未達の業務目的へ昇格する回帰を防ぐ。"""
    payload = json.loads(
        (Path(__file__).parent / "fixtures/issue_89_closure_input.json").read_text(encoding="utf-8")
    )

    result = run_closure(tmp_path, payload)

    assert result["closure_status"] == "reject"
    assert result["code_done"] == "achieved"
    assert result["merged"] == "unmet"
    assert result["purpose_achieved"] == "unmet"
    assert result["continuation"]["owner"] == "pm-controller"


@pytest.mark.parametrize(
    ("lifecycle", "owner", "next_action", "trigger", "blocker"),
    [
        ("reconnectable", "implementer", "merge-ownerへ受渡す", "review receipt受領時", None),
        ("blocked", "ci-owner", "失敗jobを修正する", "修正commit作成時", "CI失敗"),
        ("blocked", "merge-owner", "gateを再診断する", "token診断完了時", "merge block"),
        ("blocked", "pm-controller", "判断packetを採否する", "判断回答受領時", "判断待ち"),
        ("running", "pm-controller", "次のwork unitを分配する", "owner報告受領時", None),
        (
            "handover-waiting",
            "front-desk-old",
            "新rootへ明示dispatchする",
            "claim receipt成功時",
            None,
        ),
    ],
)
def test_six_continuity_cases_keep_owner_action_and_trigger(
    lifecycle, owner, next_action, trigger, blocker
):
    """6つのterminal/再開場面で次の責任と復帰条件が消える回帰を防ぐ。"""
    request = {
        "request_id": "request-45",
        "lifecycle": lifecycle,
        "owner_agent": owner,
        "next_action": next_action,
        "resume_trigger": trigger,
        "blocker": blocker,
    }

    assert continuity_errors([request]) == []
