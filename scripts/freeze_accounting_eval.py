"""Freeze public inputs before bounded accounting Agent evaluation."""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "apps/world/fixtures/accounting_known.json",
    "apps/world/fixtures/accounting_holdout_m8_missing.json",
    "apps/world/accounting_models.py",
    "apps/world/accounting_simulator.py",
    "apps/accounting_agent/provider.py",
    "apps/accounting_agent/controller.py",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "seed": 8901,
        "provider": "Codex CLI / ChatGPT login",
        "model": "CLI default (public ID unreported)",
        "cli_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "files": {relative: digest(ROOT / relative) for relative in FILES},
        "evaluator_label_included": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
