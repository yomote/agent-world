"""Front Desk引継bundleを決定的に作成・検証するローカルCLI。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
PROJECT_ID = "yomote/agent-world"
REPOSITORY = "https://github.com/yomote/agent-world"
RUNBOOK = "docs/runbooks/request-registry.md"
CLI = "scripts/manage_status_registry.py"
LOCATOR = Path(".codex/handoff-locator.local.json")
CLAIM_MARKER = Path(".codex/handoff-claim.local.json")
CONTEXT_FIELDS = {
    "schema_version",
    "source",
    "bundle_digest",
    "registry_generation_before_prepare",
    "registry_generation_after_prepare",
    "handover_state",
    "from_front_desk",
    "to_front_desk",
    "prepared_at",
    "resume_policy",
    "requests",
    "dispatch_policy",
}
REQUEST_CONTEXT_FIELDS = {
    "request_id",
    "scope_id",
    "issue_url",
    "issue_state",
    "issue_observation",
    "issue_observed_at",
    "public_title",
    "public_purpose",
    "acceptance_summary",
    "authority_source",
    "lifecycle",
    "owner_agent",
    "member_agents",
    "progress_summary",
    "blocker",
    "next_action",
    "resume_trigger",
    "dod_source_version",
    "closure_audit",
    "po_review_required",
    "po_acceptance_receipt",
    "report_updated_at",
    "report_source",
    "runtime_connection",
    "runtime_observed_at",
    "evidence",
    "worker_restart_policy",
}
LEGACY_REQUEST_CONTEXT_FIELDS = REQUEST_CONTEXT_FIELDS - {
    "resume_trigger",
    "dod_source_version",
    "closure_audit",
    "po_review_required",
    "po_acceptance_receipt",
}
CLOSURE_GATES = ("code_done", "verified", "reviewed", "merged", "delivered", "purpose")


def canonical_bytes(bundle: dict) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def digest_bundle(bundle: dict) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(bundle)).hexdigest()}"


def validate_alias(value: str) -> str:
    if not isinstance(value, str) or not ALIAS.fullmatch(value):
        raise ValueError("invalid stable Front Desk alias")
    return value


def validate_timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs an explicit timezone")
    return value


def validate_public_text(
    value: object, name: str, *, nullable: bool = False, max_length: int = 500
) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError(f"public context {name} must be a non-empty bounded string")


def validate_https(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"public context {name} must be an HTTPS URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"public context {name} must be an HTTPS URL")


def validate_context_request(item: object) -> dict:
    if (
        not isinstance(item, dict)
        or not LEGACY_REQUEST_CONTEXT_FIELDS <= set(item)
        or not set(item) <= REQUEST_CONTEXT_FIELDS
    ):
        raise ValueError("handoff public context request fields do not match the allowlist")
    validate_alias(item["request_id"])
    validate_alias(item["scope_id"])
    validate_https(item["issue_url"], "issue_url")
    if item["issue_state"] not in {"open", "closed", "unknown"}:
        raise ValueError("invalid public context Issue state")
    if item["issue_observation"] not in {"confirmed", "unavailable"}:
        raise ValueError("invalid public context Issue observation")
    validate_timestamp(item["issue_observed_at"])
    validate_public_text(item["public_title"], "public_title", max_length=240)
    for name in ("public_purpose", "acceptance_summary"):
        validate_public_text(item[name], name)
    if item["authority_source"] != "github-issue-observation":
        raise ValueError("invalid public context authority source")
    if item["lifecycle"] not in {
        "registered",
        "delegated",
        "running",
        "blocked",
        "handover-waiting",
        "reconnectable",
        "completed",
    }:
        raise ValueError("invalid public context lifecycle")
    validate_public_text(item["owner_agent"], "owner_agent", nullable=True, max_length=80)
    members = item["member_agents"]
    if (
        not isinstance(members, list)
        or len(members) > 32
        or len(members) != len(set(members))
        or any(not isinstance(member, str) or not member for member in members)
    ):
        raise ValueError("invalid public context member agents")
    for name in ("progress_summary", "blocker", "next_action"):
        validate_public_text(item[name], name, nullable=True)
    if "resume_trigger" in item:
        validate_public_text(item["resume_trigger"], "resume_trigger", nullable=True)
    validate_timestamp(item["report_updated_at"])
    if item["report_source"] != "manual-public-summary":
        raise ValueError("invalid public context report source")
    if item["runtime_connection"] not in {"connected", "record-only", "unknown"}:
        raise ValueError("invalid public context runtime connection")
    runtime_observed_at = item["runtime_observed_at"]
    if runtime_observed_at is not None:
        validate_timestamp(runtime_observed_at)
    if (item["runtime_connection"] == "unknown") != (runtime_observed_at is None):
        raise ValueError("public context runtime clock does not match connection state")
    evidence = item["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 16:
        raise ValueError("invalid public context evidence list")
    for record in evidence:
        if not isinstance(record, dict) or set(record) != {"kind", "url", "observed_at"}:
            raise ValueError("public context evidence fields do not match the allowlist")
        if record["kind"] not in {"issue", "pull-request", "review", "deployment"}:
            raise ValueError("invalid public context evidence kind")
        validate_https(record["url"], "evidence URL")
        validate_timestamp(record["observed_at"])
    if item["worker_restart_policy"] != "no-automatic-restart":
        raise ValueError("public context worker restart policy must prohibit auto restart")
    if item["lifecycle"] == "completed" and item["issue_state"] != "closed":
        raise ValueError("completed public context request needs a closed Issue")
    if (item["issue_state"] == "unknown") != (item["issue_observation"] == "unavailable"):
        raise ValueError("unknown public context Issue needs unavailable observation")
    return item


def continuity_errors(requests: list[dict]) -> list[str]:
    """未解決requestが次の責任者・行動・復帰条件を失っていないか調べる。"""
    errors: list[str] = []
    for request in requests:
        if request.get("lifecycle") == "completed":
            continue
        request_id = request.get("request_id", "unknown-request")
        for field in ("owner_agent", "next_action", "resume_trigger"):
            if not request.get(field):
                errors.append(f"{request_id}: unresolved request needs {field}")
        if request.get("lifecycle") == "blocked" and not request.get("blocker"):
            errors.append(f"{request_id}: blocked request needs blocker")
    return errors


def closure_check(input_path: Path) -> dict:
    """独立checkerの監査入力と現DoD/headを照合し、closure receiptを発行する。"""
    payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "request", "audit"}:
        raise ValueError("closure check input fields do not match schema version 1")
    if payload["schema_version"] != 1:
        raise ValueError("unsupported closure check schema")
    request = payload["request"]
    audit = payload["audit"]
    request_fields = {
        "request_id",
        "objective_ref",
        "latest_dod_version",
        "required_requirement_ids",
        "requirements_contract_digest",
        "expected_head",
        "issue_state",
        "terminal_intent",
    }
    audit_fields = {
        "schema_version",
        "request_id",
        "objective_ref",
        "dod_source_version",
        "requirements_contract_digest",
        "evidence_head",
        "checker_agent",
        "checked_at",
        *CLOSURE_GATES,
        "overall",
        "owner",
        "next_action",
        "resume_trigger",
        "stop_decision",
        "requirements",
    }
    if not isinstance(request, dict) or set(request) != request_fields:
        raise ValueError("closure request fields do not match the allowlist")
    if not isinstance(audit, dict) or set(audit) != audit_fields or audit["schema_version"] != 1:
        raise ValueError("closure audit fields do not match schema version 1")
    validate_alias(request["request_id"])
    validate_https(request["objective_ref"], "objective_ref")
    validate_public_text(request["latest_dod_version"], "latest_dod_version", max_length=200)
    required_ids = request["required_requirement_ids"]
    if (
        not isinstance(required_ids, list)
        or not required_ids
        or len(required_ids) != len(set(required_ids))
        or any(not isinstance(item, str) or not ALIAS.fullmatch(item) for item in required_ids)
    ):
        raise ValueError("closure request needs unique required requirement IDs")
    contract_digest = request["requirements_contract_digest"]
    if not isinstance(contract_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", contract_digest
    ):
        raise ValueError("closure request needs a requirements contract digest")
    if not isinstance(request["expected_head"], str) or not COMMIT.fullmatch(
        request["expected_head"]
    ):
        raise ValueError("closure request expected_head must be a full SHA")
    if request["issue_state"] not in {"open", "closed", "unknown"}:
        raise ValueError("invalid closure Issue state")
    if request["terminal_intent"] not in {"complete", "cancelled", "continue"}:
        raise ValueError("invalid closure terminal intent")

    reasons: list[str] = []
    guard_errors: list[str] = []
    states: dict[str, str] = {}
    for name in CLOSURE_GATES:
        gate = audit[name]
        if not isinstance(gate, dict) or set(gate) != {"state", "evidence_refs", "reason"}:
            raise ValueError(f"closure {name} gate fields do not match the allowlist")
        state = gate["state"]
        if state not in {"achieved", "unmet", "unknown"}:
            raise ValueError(f"invalid closure {name} state")
        evidence = gate["evidence_refs"]
        if (
            not isinstance(evidence, list)
            or len(evidence) > 16
            or any(not isinstance(reference, str) or not reference for reference in evidence)
        ):
            raise ValueError(f"invalid closure {name} evidence")
        for reference in evidence:
            validate_https(reference, f"closure {name} evidence")
        if state == "achieved" and not evidence:
            guard_errors.append(f"{name}: achieved gate has no evidence")
            state = "unmet"
        if state != "achieved" and not gate["reason"]:
            guard_errors.append(f"{name}: unresolved gate has no reason")
        elif state != "achieved":
            reasons.append(f"{name}: {gate['reason']}")
        states[name] = state

    requirements = audit["requirements"]
    if not isinstance(requirements, list) or not requirements or len(requirements) > 64:
        raise ValueError("closure audit needs a bounded requirements map")
    requirement_results: dict[str, str] = {}
    requirement_fields = {
        "requirement_id",
        "category",
        "source_version",
        "verification_method",
        "evidence_refs",
        "evidence_head",
        "checker_agent",
        "result",
        "reason",
    }
    for requirement in requirements:
        if not isinstance(requirement, dict) or set(requirement) != requirement_fields:
            raise ValueError("closure requirement fields do not match the allowlist")
        requirement_id = requirement["requirement_id"]
        validate_alias(requirement_id)
        if requirement_id in requirement_results:
            raise ValueError("closure requirement identifiers must be unique")
        if requirement["category"] not in {"generic-quality", "domain", "value"}:
            raise ValueError("invalid closure requirement category")
        validate_public_text(requirement["source_version"], "source_version", max_length=200)
        validate_public_text(requirement["verification_method"], "verification_method")
        validate_public_text(requirement["checker_agent"], "checker_agent", max_length=80)
        if requirement["evidence_head"] != request["expected_head"]:
            guard_errors.append(f"{requirement_id}: requirement evidence uses another head")
        evidence = requirement["evidence_refs"]
        if not isinstance(evidence, list) or len(evidence) > 16:
            raise ValueError("invalid closure requirement evidence")
        for reference in evidence:
            validate_https(reference, f"{requirement_id} evidence")
        result = requirement["result"]
        if result not in {"satisfied", "unmet", "unverified", "not_applicable"}:
            raise ValueError("invalid closure requirement result")
        if result == "satisfied" and not evidence:
            guard_errors.append(f"{requirement_id}: satisfied requirement has no evidence")
            result = "unmet"
        if result != "satisfied" and not requirement["reason"]:
            guard_errors.append(f"{requirement_id}: non-satisfied requirement has no reason")
        elif result in {"unmet", "unverified"}:
            reasons.append(f"{requirement_id}: {requirement['reason']}")
        if (
            requirement["category"] in {"domain", "value"}
            and requirement["source_version"] != request["latest_dod_version"]
        ):
            guard_errors.append(f"{requirement_id}: requirement uses another DoD version")
        requirement_results[requirement_id] = result
    if set(requirement_results) != set(required_ids):
        guard_errors.append("requirements map does not cover the fixed required IDs")
    if audit["requirements_contract_digest"] != contract_digest:
        guard_errors.append(
            "requirements contract digest does not match the current Issue contract"
        )

    matches = {
        "request_id": audit["request_id"] == request["request_id"],
        "objective_ref": audit["objective_ref"] == request["objective_ref"],
        "dod_source_version": audit["dod_source_version"] == request["latest_dod_version"],
        "evidence_head": audit["evidence_head"] == request["expected_head"],
    }
    guard_errors.extend(
        f"{name}: audit does not match current request" for name, ok in matches.items() if not ok
    )
    validate_public_text(audit["checker_agent"], "checker_agent", max_length=80)
    validate_timestamp(audit["checked_at"])
    stop = audit["stop_decision"]
    if not isinstance(stop, dict) or set(stop) != {"made", "reason"}:
        raise ValueError("closure stop decision fields do not match the allowlist")
    if not isinstance(stop["made"], bool) or stop["made"] != bool(stop["reason"]):
        raise ValueError("closure stop decision needs a reason only when made")

    if request["terminal_intent"] == "cancelled":
        guard_errors.append("cancelled work is not successful closure")
    if request["issue_state"] != "closed":
        guard_errors.append("successful closure needs a closed Issue observation")
    if guard_errors or "unmet" in states.values() or "unmet" in requirement_results.values():
        derived = "reject"
    elif "unknown" in states.values() or "unverified" in requirement_results.values():
        derived = "unknown"
    else:
        derived = "accept"
    if audit["overall"] != derived:
        guard_errors.append("submitted overall does not match the guarded closure result")
        derived = "reject"

    continuation = {
        "owner": audit["owner"],
        "next_action": audit["next_action"],
        "resume_trigger": audit["resume_trigger"],
    }
    if derived == "accept":
        if any(value is not None for value in continuation.values()):
            guard_errors.append("accepted closure cannot retain continuation fields")
            derived = "reject"
    else:
        for name, value in continuation.items():
            if not value:
                guard_errors.append(f"unaccepted closure needs {name}")

    normalized = {**audit, "overall": derived}
    return {
        "request_id": request["request_id"],
        "closure_status": derived,
        "code_done": states["code_done"],
        "verified": states["verified"],
        "reviewed": states["reviewed"],
        "merged": states["merged"],
        "delivered": states["delivered"],
        "purpose_achieved": states["purpose"],
        "requirements": requirement_results,
        "receipt": normalized if derived == "accept" else None,
        "continuation": None if derived == "accept" else continuation,
        "reasons": [*reasons, *guard_errors],
        "receipt_authenticity": "status-ingest-authenticated-but-not-signed",
    }


def prepare_update(bundle: dict, digest: str) -> dict:
    return {
        "source": "manual-public-registry",
        "action": "prepare-handover",
        "expected_generation": bundle["expected_generation"],
        "actor_front_desk": bundle["from_front_desk"],
        "observed_at": bundle["prepared_at"],
        "requests": [],
        "successor_front_desk": bundle["to_front_desk"],
        "bundle_digest": digest,
    }


def verify_artifact(artifact_path: Path) -> tuple[dict, str]:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    bundle = artifact["bundle"]
    expected = digest_bundle(bundle)
    if artifact.get("bundle_digest") != expected:
        raise ValueError("handover bundle digest does not match canonical UTF-8 JSON")
    if artifact.get("update") != prepare_update(bundle, expected):
        raise ValueError("handover prepare update does not match its bundle")
    return bundle, expected


def read_registry(registry_path: Path) -> dict:
    registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    generation = registry.get("generation")
    active = registry.get("active_front_desk")
    requests = registry.get("requests")
    if not isinstance(generation, int) or generation < 1:
        raise ValueError("registry generation must be a positive integer")
    if not isinstance(active, dict) or not isinstance(requests, list):
        raise ValueError("registry needs active_front_desk and requests")
    validate_alias(active.get("alias"))
    return {
        "generation": generation,
        "active_front_desk": active,
        "handover": registry.get("handover"),
        "requests": [
            {
                "request_id": request["request_id"],
                "issue_url": request["issue_url"],
                "lifecycle": request["lifecycle"],
                "runtime_connection": request["runtime_connection"],
                "owner_agent": request.get("owner_agent"),
                "blocker": request.get("blocker"),
                "next_action": request.get("next_action"),
                "resume_trigger": request.get("resume_trigger"),
                "dod_source_version": request.get("dod_source_version"),
                "closure_audit": request.get("closure_audit"),
                "po_review_required": request.get("po_review_required", False),
                "po_acceptance_receipt": request.get("po_acceptance_receipt"),
                "report_updated_at": request["report_updated_at"],
            }
            for request in requests
        ],
    }


def boot(artifact_path: Path) -> dict:
    bundle, digest = verify_artifact(artifact_path)
    return {
        "bundle_digest": digest,
        "claim_expected_generation": bundle["expected_generation"] + 1,
        "from_front_desk": bundle["from_front_desk"],
        "to_front_desk": bundle["to_front_desk"],
        "resume_policy": bundle["resume_policy"],
        "requests": bundle["requests"],
        "claim_required": True,
        "workers_started": False,
    }


def discover(locator_path: Path, project_root: Path) -> dict:
    """project-local locatorと凍結bundleをread-onlyで照合する。"""
    project_root = project_root.resolve()
    expected_locator = (project_root / LOCATOR).resolve()
    if locator_path.resolve() != expected_locator:
        raise ValueError("handoff locator must use the project-local standard path")
    if not locator_path.exists():
        return {
            "local_handover_candidate": False,
            "reason": "locator-not-found",
            "claim_required": False,
            "workers_started": False,
        }
    locator_bytes = locator_path.read_bytes()
    locator_digest = f"sha256:{hashlib.sha256(locator_bytes).hexdigest()}"
    locator = json.loads(locator_bytes.decode("utf-8-sig"))
    required = {
        "schema_version",
        "project_id",
        "repo_url",
        "registry_source_commit",
        "runbook",
        "cli",
        "bundle_path",
        "start_path",
        "context_path",
        "context_digest",
        "expected_generation",
        "bundle_digest",
        "from_front_desk",
        "to_front_desk",
        "claim_executed",
        "claim_policy",
    }
    if set(locator) != required:
        raise ValueError("handoff locator fields do not match schema version 1")
    if locator["schema_version"] != 1:
        raise ValueError("unsupported handoff locator schema")
    if locator["project_id"] != PROJECT_ID or locator["repo_url"] != REPOSITORY:
        raise ValueError("handoff locator belongs to a different project")
    if locator["runbook"] != RUNBOOK or locator["cli"] != CLI:
        raise ValueError("handoff locator must use the canonical registry paths")
    source_commit = locator["registry_source_commit"]
    if not isinstance(source_commit, str) or not COMMIT.fullmatch(source_commit):
        raise ValueError("registry source commit must be a full SHA")
    if locator["claim_policy"] != "new-top-level-root-explicit-claim-only":
        raise ValueError("handoff locator claim policy is not supported")
    if locator["claim_executed"] is not False:
        raise ValueError("handoff locator is already claimed or has unknown claim state")
    marker_path = project_root / CLAIM_MARKER
    if marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf-8-sig"))
        if marker.get("locator_digest") == locator_digest:
            raise ValueError("handoff locator already has a successful claim marker")
    runbook = (project_root / locator["runbook"]).resolve()
    cli = (project_root / locator["cli"]).resolve()
    if project_root not in runbook.parents or project_root not in cli.parents:
        raise ValueError("registry paths must stay within the project root")
    if not runbook.is_file() or not cli.is_file():
        raise ValueError("project checkout does not contain the registry runbook and CLI")
    for revision in (
        f"{source_commit}^{{commit}}",
        f"{source_commit}:{RUNBOOK}",
        f"{source_commit}:{CLI}",
    ):
        result = subprocess.run(
            ["git", "-C", str(project_root), "cat-file", "-e", revision],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError("registry source commit or canonical paths are not present in git")
    bundle_path = Path(locator["bundle_path"])
    start_path = Path(locator["start_path"])
    context_path = Path(locator["context_path"])
    if (
        not bundle_path.is_absolute()
        or not start_path.is_absolute()
        or not context_path.is_absolute()
        or not start_path.is_file()
        or not context_path.is_file()
    ):
        raise ValueError("handoff bundle, context, and start document need existing absolute paths")
    restored = boot(bundle_path)
    checks = {
        "expected_generation": restored["claim_expected_generation"],
        "bundle_digest": restored["bundle_digest"],
        "from_front_desk": restored["from_front_desk"],
        "to_front_desk": restored["to_front_desk"],
    }
    if any(locator[key] != value for key, value in checks.items()):
        raise ValueError("handoff locator does not match its frozen bundle")
    context_artifact = json.loads(context_path.read_text(encoding="utf-8-sig"))
    if set(context_artifact) != {"context", "context_digest"}:
        raise ValueError("handoff public context artifact fields do not match the allowlist")
    context = context_artifact.get("context")
    if not isinstance(context, dict) or set(context) != CONTEXT_FIELDS:
        raise ValueError("handoff public context fields do not match the allowlist")
    context_digest = digest_bundle(context)
    if (
        context_artifact.get("context_digest") != context_digest
        or locator["context_digest"] != context_digest
    ):
        raise ValueError("handoff public context digest does not match")
    context_requests = context.get("requests")
    if not isinstance(context_requests, list):
        raise ValueError("handoff public context requests must be a list")
    validated_requests = [validate_context_request(item) for item in context_requests]
    context_by_id = {item["request_id"]: item for item in validated_requests}
    bundle_by_id = {item["request_id"]: item for item in restored["requests"]}
    if (
        len(context_by_id) != len(validated_requests)
        or len(bundle_by_id) != len(restored["requests"])
        or len(context_by_id) != len(bundle_by_id)
        or context_by_id.keys() != bundle_by_id.keys()
    ):
        raise ValueError("handoff public context request IDs must uniquely match the bundle")
    legacy_bundle_fields = {
        "request_id",
        "scope_id",
        "issue_url",
        "lifecycle",
        "next_action",
        "report_updated_at",
    }
    extended_bundle_fields = legacy_bundle_fields | {
        "owner_agent",
        "blocker",
        "resume_trigger",
        "dod_source_version",
        "closure_audit",
        "po_review_required",
        "po_acceptance_receipt",
    }
    if any(
        (
            set(bundle_by_id[request_id]) != legacy_bundle_fields
            and set(bundle_by_id[request_id]) != extended_bundle_fields
        )
        or {key: context_by_id[request_id][key] for key in set(bundle_by_id[request_id])}
        != {key: bundle_by_id[request_id][key] for key in set(bundle_by_id[request_id])}
        for request_id in bundle_by_id
    ):
        raise ValueError("handoff public context request values do not match the bundle")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8-sig"))["bundle"]
    if (
        context.get("schema_version") != 1
        or context.get("source") != "final-registry-snapshot-and-prepare-receipt"
        or context.get("bundle_digest") != restored["bundle_digest"]
        or context.get("registry_generation_before_prepare")
        != restored["claim_expected_generation"] - 1
        or context.get("registry_generation_after_prepare") != restored["claim_expected_generation"]
        or context.get("handover_state") != "ready"
        or context.get("from_front_desk") != restored["from_front_desk"]
        or context.get("to_front_desk") != restored["to_front_desk"]
        or validate_timestamp(context.get("prepared_at")) != bundle["prepared_at"]
        or context.get("resume_policy") != "explicit-dispatch-required"
        or context.get("dispatch_policy") != "successful-claim-receipt-then-explicit-dispatch-only"
    ):
        raise ValueError("handoff public context does not match the ready bundle")
    return {
        "local_handover_candidate": True,
        "project_id": PROJECT_ID,
        "registry_source_commit": source_commit,
        "locator_digest": locator_digest,
        **checks,
        "bundle_path": str(bundle_path),
        "start_path": str(start_path),
        "context_path": str(context_path),
        "context_digest": context_digest,
        "request_context": validated_requests,
        "claim_policy": locator["claim_policy"],
        "claim_required": True,
        "workers_started": False,
        "server_state_verified": False,
        "ownership_transferred": False,
        "server_cas_still_required": True,
    }


def mark_claimed(
    locator_path: Path, claim_path: Path, receipt_path: Path, project_root: Path
) -> dict:
    """成功claim receipt確認後だけbundle digest紐付きmarkerを保存する。"""
    candidate = discover(locator_path, project_root)
    claim_payload = json.loads(claim_path.read_text(encoding="utf-8-sig"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    expected_generation = candidate["expected_generation"] + 1
    active = receipt.get("active_front_desk")
    handover = receipt.get("handover")
    claimed_runtime = claim_payload.get("actor_runtime_session_id")
    valid = (
        claim_payload.get("action") == "claim-handover"
        and claim_payload.get("expected_generation") == candidate["expected_generation"]
        and claim_payload.get("actor_front_desk") == candidate["to_front_desk"]
        and claim_payload.get("successor_front_desk") == candidate["to_front_desk"]
        and claim_payload.get("bundle_digest") == candidate["bundle_digest"]
        and isinstance(claimed_runtime, str)
        and bool(claimed_runtime)
        and receipt.get("changed") is True
        and receipt.get("generation") == expected_generation
        and isinstance(active, dict)
        and active.get("alias") == candidate["to_front_desk"]
        and active.get("runtime_session_id") == claimed_runtime
        and isinstance(handover, dict)
        and handover.get("state") == "accepted"
        and handover.get("to_front_desk") == candidate["to_front_desk"]
        and handover.get("bundle_digest") == candidate["bundle_digest"]
    )
    if not valid:
        raise ValueError("claim receipt does not prove the expected owner transfer")
    if (
        f"sha256:{hashlib.sha256(locator_path.read_bytes()).hexdigest()}"
        != candidate["locator_digest"]
    ):
        raise ValueError("handoff locator changed before claim marker write")
    marker_path = project_root.resolve() / CLAIM_MARKER
    temporary = marker_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "locator_digest": candidate["locator_digest"],
                "bundle_digest": candidate["bundle_digest"],
                "generation": receipt["generation"],
                "active_front_desk": active["alias"],
                "runtime_session_id": claimed_runtime,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(marker_path)
    return {
        "claim_marked": True,
        "generation": receipt["generation"],
        "active_front_desk": active["alias"],
        "runtime_binding": {
            "registry_generation": receipt["generation"],
            "front_desk_alias": active["alias"],
            "runtime_session_digest": (
                f"sha256:{hashlib.sha256(claimed_runtime.encode()).hexdigest()}"
            ),
        },
        "workers_started": False,
    }


def prepare(registry_path: Path, successor: str, prepared_at: str) -> dict:
    registry = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    generation = registry.get("generation")
    active = registry.get("active_front_desk", {})
    requests = registry.get("requests")
    if not isinstance(generation, int) or generation < 1 or not isinstance(requests, list):
        raise ValueError("registry needs a positive generation and request list")
    from_alias = validate_alias(active.get("alias"))
    validate_alias(successor)
    validate_timestamp(prepared_at)
    gaps = continuity_errors(requests)
    if gaps:
        raise ValueError("handoff continuity check failed: " + "; ".join(gaps))
    bundle = {
        "schema_version": 1,
        "expected_generation": generation,
        "from_front_desk": from_alias,
        "to_front_desk": successor,
        "prepared_at": prepared_at,
        "resume_policy": "explicit-dispatch-required",
        "requests": [
            {
                "request_id": item["request_id"],
                "scope_id": item["scope_id"],
                "issue_url": item["issue_url"],
                "lifecycle": item["lifecycle"],
                "owner_agent": item.get("owner_agent"),
                "blocker": item.get("blocker"),
                "next_action": item.get("next_action"),
                "resume_trigger": item.get("resume_trigger"),
                "dod_source_version": item.get("dod_source_version"),
                "closure_audit": item.get("closure_audit"),
                "po_review_required": item.get("po_review_required", False),
                "po_acceptance_receipt": item.get("po_acceptance_receipt"),
                "report_updated_at": item["report_updated_at"],
            }
            for item in sorted(requests, key=lambda value: value["request_id"])
        ],
    }
    digest = digest_bundle(bundle)
    return {"bundle": bundle, "bundle_digest": digest, "update": prepare_update(bundle, digest)}


def claim(artifact_path: Path, actor: str, observed_at: str, runtime_session_id: str) -> dict:
    bundle, expected = verify_artifact(artifact_path)
    if bundle.get("to_front_desk") != actor:
        raise ValueError("handover bundle names a different successor")
    validate_alias(actor)
    validate_timestamp(observed_at)
    return {
        "source": "manual-public-registry",
        "action": "claim-handover",
        "expected_generation": bundle["expected_generation"] + 1,
        "actor_front_desk": actor,
        "actor_runtime_session_id": runtime_session_id,
        "observed_at": observed_at,
        "requests": [],
        "successor_front_desk": actor,
        "bundle_digest": expected,
    }


def root_runtime_session_id(environment: Mapping[str, str], canonical_task_path: str) -> str:
    """child自身ではなく、継承されたtop-level rootのruntime IDを返す。"""
    thread_id = environment.get("CODEX_THREAD_ID", "")
    child_id = environment.get("CODEX_SESSION_ID", "")
    try:
        UUID(thread_id)
    except ValueError as error:
        raise ValueError("CODEX_THREAD_ID must identify the top-level root runtime") from error
    try:
        UUID(child_id)
    except ValueError as error:
        raise ValueError("CODEX_SESSION_ID must identify the delegated worker") from error
    if thread_id == child_id:
        raise ValueError("root claim must run from a delegated worker with a distinct session ID")
    if not re.fullmatch(r"/root(?:/[a-z0-9_]+)+", canonical_task_path):
        raise ValueError("root claim needs delegated runtime canonical task path metadata")
    return thread_id


def claim_root(artifact_path: Path, actor: str, observed_at: str, canonical_task_path: str) -> dict:
    return claim(
        artifact_path,
        actor,
        observed_at,
        root_runtime_session_id(os.environ, canonical_task_path),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("--registry", type=Path, required=True)
    read_parser.add_argument("--output", type=Path, required=True)
    boot_parser = subparsers.add_parser("boot")
    boot_parser.add_argument("--bundle", type=Path, required=True)
    boot_parser.add_argument("--output", type=Path, required=True)
    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument(
        "--locator", type=Path, default=Path(".codex/handoff-locator.local.json")
    )
    discover_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    discover_parser.add_argument("--output", type=Path, required=True)
    mark_parser = subparsers.add_parser("mark-claimed")
    mark_parser.add_argument("--locator", type=Path, default=LOCATOR)
    mark_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    mark_parser.add_argument("--claim-payload", type=Path, required=True)
    mark_parser.add_argument("--receipt", type=Path, required=True)
    mark_parser.add_argument("--output", type=Path, required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--registry", type=Path, required=True)
    prepare_parser.add_argument("--successor", required=True)
    prepare_parser.add_argument("--observed-at", required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    claim_root_parser = subparsers.add_parser("claim-root")
    claim_root_parser.add_argument("--bundle", type=Path, required=True)
    claim_root_parser.add_argument("--actor", required=True)
    claim_root_parser.add_argument("--observed-at", required=True)
    claim_root_parser.add_argument("--canonical-task-path", required=True)
    claim_root_parser.add_argument("--output", type=Path, required=True)
    closure_parser = subparsers.add_parser("closure-check")
    closure_parser.add_argument("--input", type=Path, required=True)
    closure_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "read":
        result = read_registry(args.registry)
    elif args.command == "boot":
        result = boot(args.bundle)
    elif args.command == "discover":
        result = discover(args.locator, args.project_root)
    elif args.command == "mark-claimed":
        result = mark_claimed(args.locator, args.claim_payload, args.receipt, args.project_root)
    elif args.command == "prepare":
        result = prepare(args.registry, args.successor, args.observed_at)
    elif args.command == "closure-check":
        result = closure_check(args.input)
    else:
        result = claim_root(args.bundle, args.actor, args.observed_at, args.canonical_task_path)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
