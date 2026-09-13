"""Front Desk引継bundleを決定的に作成・検証するローカルCLI。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ops_status.models import RequestRegistrySnapshot, RequestRegistryUpdate


def canonical_bytes(bundle: dict) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def digest_bundle(bundle: dict) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(bundle)).hexdigest()}"


def verify_artifact(artifact_path: Path) -> tuple[dict, str]:
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    bundle = artifact["bundle"]
    expected = digest_bundle(bundle)
    if artifact.get("bundle_digest") != expected:
        raise ValueError("handover bundle digest does not match canonical UTF-8 JSON")
    return bundle, expected


def read_registry(registry_path: Path) -> dict:
    registry = RequestRegistrySnapshot.model_validate_json(
        registry_path.read_text(encoding="utf-8")
    )
    return {
        "generation": registry.generation,
        "active_front_desk": registry.active_front_desk.model_dump(mode="json"),
        "handover": registry.handover.model_dump(mode="json") if registry.handover else None,
        "requests": [
            {
                "request_id": request.request_id,
                "issue_url": str(request.issue_url),
                "lifecycle": request.lifecycle,
                "runtime_connection": request.runtime_connection,
                "next_action": request.next_action,
                "report_updated_at": request.report_updated_at.isoformat(),
            }
            for request in registry.requests
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


def prepare(registry_path: Path, successor: str, prepared_at: str) -> dict:
    registry = RequestRegistrySnapshot.model_validate_json(
        registry_path.read_text(encoding="utf-8")
    )
    bundle = {
        "schema_version": 1,
        "expected_generation": registry.generation,
        "from_front_desk": registry.active_front_desk.alias,
        "to_front_desk": successor,
        "prepared_at": prepared_at,
        "resume_policy": "explicit-dispatch-required",
        "requests": [
            {
                "request_id": item.request_id,
                "scope_id": item.scope_id,
                "issue_url": str(item.issue_url),
                "lifecycle": item.lifecycle,
                "next_action": item.next_action,
                "report_updated_at": item.report_updated_at.isoformat(),
            }
            for item in sorted(registry.requests, key=lambda value: value.request_id)
        ],
    }
    digest = digest_bundle(bundle)
    update = RequestRegistryUpdate(
        source="manual-public-registry",
        action="prepare-handover",
        expected_generation=registry.generation,
        actor_front_desk=registry.active_front_desk.alias,
        observed_at=prepared_at,
        successor_front_desk=successor,
        bundle_digest=digest,
    )
    return {"bundle": bundle, "bundle_digest": digest, "update": update.model_dump(mode="json")}


def claim(artifact_path: Path, actor: str, observed_at: str) -> dict:
    bundle, expected = verify_artifact(artifact_path)
    if bundle.get("to_front_desk") != actor:
        raise ValueError("handover bundle names a different successor")
    update = RequestRegistryUpdate(
        source="manual-public-registry",
        action="claim-handover",
        expected_generation=bundle["expected_generation"] + 1,
        actor_front_desk=actor,
        observed_at=observed_at,
        successor_front_desk=actor,
        bundle_digest=expected,
    )
    return update.model_dump(mode="json")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("--registry", type=Path, required=True)
    read_parser.add_argument("--output", type=Path, required=True)
    boot_parser = subparsers.add_parser("boot")
    boot_parser.add_argument("--bundle", type=Path, required=True)
    boot_parser.add_argument("--output", type=Path, required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--registry", type=Path, required=True)
    prepare_parser.add_argument("--successor", required=True)
    prepare_parser.add_argument("--observed-at", required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    claim_parser = subparsers.add_parser("claim")
    claim_parser.add_argument("--bundle", type=Path, required=True)
    claim_parser.add_argument("--actor", required=True)
    claim_parser.add_argument("--observed-at", required=True)
    claim_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "read":
        result = read_registry(args.registry)
    elif args.command == "boot":
        result = boot(args.bundle)
    elif args.command == "prepare":
        result = prepare(args.registry, args.successor, args.observed_at)
    else:
        result = claim(args.bundle, args.actor, args.observed_at)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
