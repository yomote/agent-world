"""正規化済みsnapshotを検証し、local status storeへ原子的に保存する。"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("pm-confirmed", "fixture"), required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/status/current.json"))
    return parser.parse_args()


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
    from ops_status.models import StatusSnapshot

    args = parse_args()
    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        payload = json.load(sys.stdin)
    received_at = datetime.now(UTC)
    snapshot = StatusSnapshot.model_validate(
        {
            "schema_version": 1,
            "source": args.source,
            "observed_at": args.observed_at,
            "received_at": received_at,
            "items": payload,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(f"{args.output.suffix}.{os.getpid()}.tmp")
    temporary.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(args.output)
    print(f"status snapshot written: source={snapshot.source} items={len(snapshot.items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
