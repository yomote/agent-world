"""保存済みbilling観測を検証し、欠落を補完せず正規化するローカルCLI。"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 128 * 1024
MAX_OBSERVATIONS = 256
ROOT_FIELDS = {"schema_version", "source_id", "observations"}
OBSERVATION_FIELDS = {"check", "status", "currency", "observed_at", "source_ref"}
STATUSES = {"failed", "unknown", "not_run", "pass"}
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/#@?&=%+~-]{0,511}")
CHECK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


class InputError(ValueError):
    """入力値を含まない固定理由とフィールド位置だけを通知する。"""


def require_object(value, allowed, required, location):
    if not isinstance(value, dict):
        raise InputError(f"{location}: object_required")
    if set(value) - allowed:
        raise InputError(f"{location}: unexpected_field")
    for field in sorted(required):
        if field not in value:
            raise InputError(f"{location}.{field}: missing_field")


def require_text(value, pattern, location):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise InputError(f"{location}: invalid_text")
    return value


def optional_text(value, location):
    if value is None:
        return None
    if not isinstance(value, str):
        raise InputError(f"{location}: string_or_null_required")
    if not value.strip():
        return None
    return value


def normalize_time(value, location):
    value = optional_text(value, location)
    if value is None:
        return None
    if TIMESTAMP.fullmatch(value) is None or value.endswith("-00:00"):
        raise InputError(f"{location}: invalid_timestamp")
    try:
        observed = datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise InputError(f"{location}: invalid_timestamp") from exc
    return observed.isoformat().replace("+00:00", "Z")


def normalize(document):
    """statusを保存し、通貨の欠落とpassの根拠不足は成功出力にしない。"""
    require_object(document, ROOT_FIELDS, ROOT_FIELDS, "$")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise InputError("$.schema_version: unsupported_schema_version")
    source_id = require_text(document["source_id"], REFERENCE, "$.source_id")
    observations = document["observations"]
    if not isinstance(observations, list) or not 1 <= len(observations) <= MAX_OBSERVATIONS:
        raise InputError("$.observations: nonempty_bounded_array_required")
    normalized = []
    for index, item in enumerate(observations):
        location = f"$.observations[{index}]"
        require_object(item, OBSERVATION_FIELDS, {"check", "status", "currency"}, location)
        check = require_text(item["check"], CHECK, f"{location}.check")
        status = item["status"]
        if not isinstance(status, str) or status not in STATUSES:
            raise InputError(f"{location}.status: invalid_status")
        currency = item["currency"]
        if not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
            raise InputError(f"{location}.currency: invalid_currency")
        observed_at = normalize_time(item.get("observed_at"), f"{location}.observed_at")
        source_ref = optional_text(item.get("source_ref"), f"{location}.source_ref")
        if source_ref is not None:
            source_ref = require_text(source_ref, REFERENCE, f"{location}.source_ref")
        if status == "pass":
            for field, value in (("observed_at", observed_at), ("source_ref", source_ref)):
                if value is None:
                    raise InputError(f"{location}.{field}: pass_requires_evidence")
        normalized.append(
            {
                "check": check,
                "status": status,
                "currency": currency,
                "observed_at": observed_at,
                "source_ref": source_ref,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "source_id": source_id, "observations": normalized}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("$: duplicate_key")
        result[key] = value
    return result


def reject_constant(value):
    raise InputError("$: non_json_number")


def parse_document(raw):
    """重複キー、非JSON数値、壊れたUTF-8を捨てずに拒否する。"""
    if len(raw) > MAX_INPUT_BYTES:
        raise InputError("$: input_too_large")
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
        )
    except InputError:
        raise
    except (ValueError, RecursionError) as exc:
        raise InputError("$: invalid_json_or_utf8") from exc
    return normalize(document)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("input", type=Path, help="保存済みUTF-8 JSONのローカルパス")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as stream:
            result = parse_document(stream.read(MAX_INPUT_BYTES + 1))
    except InputError as exc:
        print(f"入力拒否: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("入力読取失敗: input_read_failed", file=sys.stderr)
        return 3
    # 全観測を検証してから一括出力する。出典文字列を開いたり実行したりしない。
    print(json.dumps(result, ensure_ascii=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
