"""Front Desk引継bundleを決定的に作成・検証するローカルCLI。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


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
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs an explicit timezone")
    return value


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
                "next_action": request.get("next_action"),
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
                "next_action": item.get("next_action"),
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
    claim_parser.add_argument("--runtime-session-id", required=True)
    claim_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "read":
        result = read_registry(args.registry)
    elif args.command == "boot":
        result = boot(args.bundle)
    elif args.command == "prepare":
        result = prepare(args.registry, args.successor, args.observed_at)
    else:
        result = claim(args.bundle, args.actor, args.observed_at, args.runtime_session_id)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
