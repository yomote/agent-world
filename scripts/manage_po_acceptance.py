"""内部closure後のPO本人確認packageとreceiptをローカルで管理する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

PACKAGE_FIELDS = {
    "schema_version",
    "kind",
    "request_id",
    "objective_ref",
    "value_summary",
    "artifact_url",
    "preview_url",
    "artifact_valid_until",
    "expected_actions",
    "verified",
    "unknowns",
    "limits",
    "evidence_head",
    "pr_url",
    "merge_state",
    "internal_receipt",
    "po_questions",
    "owner",
    "po_review_required",
}


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def validate_text(value: object, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value or len(value) > 500:
        raise ValueError(f"{name} must be bounded non-empty text")


def validate_url(value: object, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    validate_text(value, name)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{name} must be an HTTPS URL")


def validate_time(value: object, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    validate_text(value, name)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{name} needs an explicit timezone")


def validate_list(value: object, name: str, *, minimum: int = 0, maximum: int = 16) -> None:
    if (
        not isinstance(value, list)
        or not minimum <= len(value) <= maximum
        or any(not isinstance(item, str) or not item or len(item) > 500 for item in value)
    ):
        raise ValueError(f"{name} must contain {minimum}..{maximum} bounded entries")


def validate_package(package: object) -> dict:
    if not isinstance(package, dict) or set(package) != PACKAGE_FIELDS:
        raise ValueError("PO acceptance package fields do not match schema version 1")
    if package["schema_version"] != 1 or package["kind"] not in {
        "po-review",
        "unmet-decision-report",
    }:
        raise ValueError("unsupported PO acceptance package")
    for name in ("request_id", "value_summary", "owner"):
        validate_text(package[name], name)
    for name in ("objective_ref", "artifact_url", "pr_url"):
        validate_url(package[name], name)
    validate_url(package["preview_url"], "preview_url", nullable=True)
    validate_time(package["artifact_valid_until"], "artifact_valid_until", nullable=True)
    validate_list(package["expected_actions"], "expected_actions", minimum=1, maximum=3)
    validate_list(package["verified"], "verified")
    validate_list(package["unknowns"], "unknowns")
    validate_list(package["limits"], "limits")
    validate_list(package["po_questions"], "po_questions", minimum=1, maximum=3)
    head = package["evidence_head"]
    if (
        not isinstance(head, str)
        or len(head) != 40
        or any(c not in "0123456789abcdef" for c in head)
    ):
        raise ValueError("evidence_head must be a full SHA")
    if package["merge_state"] not in {"merged", "not-merged", "unknown"}:
        raise ValueError("invalid merge_state")
    receipt = package["internal_receipt"]
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version",
        "request_id",
        "dod_source_version",
        "requirements_contract_digest",
        "evidence_head",
        "overall",
    }:
        raise ValueError("internal receipt summary fields do not match the allowlist")
    if (
        receipt["schema_version"] != 1
        or receipt["request_id"] != package["request_id"]
        or receipt["evidence_head"] != head
    ):
        raise ValueError("internal receipt does not match the package")
    if package["kind"] == "po-review":
        if receipt["overall"] != "accept" or package["po_review_required"] is not True:
            raise ValueError("PO review package needs an accepted internal closure receipt")
    elif receipt["overall"] == "accept" or package["po_review_required"] is not False:
        raise ValueError("unmet decision report cannot be a completion review package")
    return package


def dedup_key(package: dict) -> str:
    identity = {
        "request_id": package["request_id"],
        "evidence_head": package["evidence_head"],
        "artifact_url": package["artifact_url"],
        "receipt_version": package["internal_receipt"]["dod_source_version"],
        "requirements_contract_digest": package["internal_receipt"]["requirements_contract_digest"],
    }
    return f"sha256:{hashlib.sha256(canonical(identity)).hexdigest()}"


def read_store(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "entries": {}}
    store = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(store, dict) or set(store) != {"schema_version", "entries"}:
        raise ValueError("PO acceptance store is invalid")
    if store["schema_version"] != 1 or not isinstance(store["entries"], dict):
        raise ValueError("unsupported PO acceptance store")
    return store


def write_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def enqueue(store_path: Path, package_path: Path) -> dict:
    package = validate_package(json.loads(package_path.read_text(encoding="utf-8-sig")))
    key = dedup_key(package)
    store = read_store(store_path)
    existing = store["entries"].get(key)
    if existing is not None:
        if existing["package"] != package:
            raise ValueError("dedup identity already has different package content")
        return {"changed": False, "dedup_key": key, "state": existing["state"]}
    state = "ready_for_po_review" if package["kind"] == "po-review" else "decision_report_ready"
    store["entries"][key] = {"package": package, "state": state}
    write_store(store_path, store)
    return {"changed": True, "dedup_key": key, "state": state}


def mark_notified(store_path: Path, key: str, receipt_path: Path) -> dict:
    store = read_store(store_path)
    entry = store["entries"].get(key)
    if entry is None or entry["state"] != "ready_for_po_review":
        raise ValueError("package is not ready for PO notification")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if not isinstance(receipt, dict) or set(receipt) != {
        "channel",
        "status",
        "observed_at",
        "message_ref",
    }:
        raise ValueError("notification receipt fields do not match the allowlist")
    if receipt["channel"] != "front-desk-same-thread" or receipt["status"] not in {
        "accepted",
        "unknown",
    }:
        raise ValueError("unsupported notification receipt")
    validate_time(receipt["observed_at"], "observed_at")
    validate_text(receipt["message_ref"], "message_ref", nullable=receipt["status"] == "unknown")
    entry["notification"] = receipt
    entry["state"] = "notified" if receipt["status"] == "accepted" else "notification_unknown"
    write_store(store_path, store)
    return {"dedup_key": key, "state": entry["state"], "human_read_verified": False}


def acknowledge(store_path: Path, key: str, response_path: Path) -> dict:
    store = read_store(store_path)
    entry = store["entries"].get(key)
    if entry is None or entry["state"] != "notified":
        raise ValueError("only a notified package can receive PO acknowledgement")
    response = json.loads(response_path.read_text(encoding="utf-8-sig"))
    required = {
        "decision",
        "acknowledged_at",
        "channel",
        "message_ref",
        "owner",
        "next_action",
        "resume_trigger",
    }
    if not isinstance(response, dict) or set(response) != required:
        raise ValueError("PO acknowledgement fields do not match the allowlist")
    if response["decision"] not in {"accepted", "changes_requested"}:
        raise ValueError("unsupported PO decision")
    validate_time(response["acknowledged_at"], "acknowledged_at")
    if response["channel"] != "front-desk-same-thread":
        raise ValueError("PO acknowledgement must come from the Front Desk thread")
    validate_text(response["message_ref"], "message_ref")
    continuation = (response["owner"], response["next_action"], response["resume_trigger"])
    if response["decision"] == "accepted" and any(value is not None for value in continuation):
        raise ValueError("accepted PO acknowledgement cannot retain continuation")
    if response["decision"] == "changes_requested":
        for name, value in zip(
            ("owner", "next_action", "resume_trigger"), continuation, strict=True
        ):
            validate_text(value, name)
    entry["po_response"] = response
    entry["state"] = response["decision"]
    write_store(store_path, store)
    receipt = None
    if response["decision"] == "accepted":
        package = entry["package"]
        receipt = {
            "schema_version": 1,
            "dedup_key": key,
            "request_id": package["request_id"],
            "evidence_head": package["evidence_head"],
            "dod_source_version": package["internal_receipt"]["dod_source_version"],
            "requirements_contract_digest": package["internal_receipt"][
                "requirements_contract_digest"
            ],
            "decision": "accepted",
            "acknowledged_at": response["acknowledged_at"],
            "channel": response["channel"],
            "message_ref": response["message_ref"],
        }
    return {"dedup_key": key, "state": entry["state"], "po_acceptance_receipt": receipt}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    enqueue_parser = sub.add_parser("enqueue")
    enqueue_parser.add_argument("--store", type=Path, required=True)
    enqueue_parser.add_argument("--package", type=Path, required=True)
    notify_parser = sub.add_parser("mark-notified")
    notify_parser.add_argument("--store", type=Path, required=True)
    notify_parser.add_argument("--dedup-key", required=True)
    notify_parser.add_argument("--receipt", type=Path, required=True)
    ack_parser = sub.add_parser("ack")
    ack_parser.add_argument("--store", type=Path, required=True)
    ack_parser.add_argument("--dedup-key", required=True)
    ack_parser.add_argument("--response", type=Path, required=True)
    for command in (enqueue_parser, notify_parser, ack_parser):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "enqueue":
        result = enqueue(args.store, args.package)
    elif args.command == "mark-notified":
        result = mark_notified(args.store, args.dedup_key, args.receipt)
    else:
        result = acknowledge(args.store, args.dedup_key, args.response)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
