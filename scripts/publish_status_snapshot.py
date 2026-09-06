"""新しいlocal event snapshotをAzure管理statusへ一度だけ送る。"""

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--audience", required=True)
    parser.add_argument("--ingest-client-id", required=True)
    parser.add_argument("--snapshot", type=Path, default=Path("artifacts/status/current.json"))
    parser.add_argument("--state", type=Path, default=Path("artifacts/status/publish-state.json"))
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    return parser.parse_args()


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def azure_cli_token(audience: str, expected_client_id: str) -> str:
    azure_cli = shutil.which("az") or r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
    if not Path(azure_cli).is_file():
        raise RuntimeError("Azure CLI was not found")
    account = subprocess.run(
        [azure_cli, "account", "show", "--output", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    user = json.loads(account.stdout)["user"]
    if user.get("type") != "servicePrincipal" or user.get("name") != expected_client_id:
        raise RuntimeError("Azure CLI is not signed in as the approved ingest identity")
    token = subprocess.run(
        [
            azure_cli,
            "account",
            "get-access-token",
            "--resource",
            audience,
            "--query",
            "accessToken",
            "--output",
            "tsv",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not token:
        raise RuntimeError("Azure CLI returned an empty access token")
    return token


def send_once(url: str, token: str, payload: bytes) -> int:
    request = urllib.request.Request(
        url,
        data=payload,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "agent-world-local-status-publisher",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status


def publish_if_new(
    args: argparse.Namespace,
    token_provider: Callable[[str, str], str] = azure_cli_token,
    sender: Callable[[str, str, bytes], int] = send_once,
) -> bool:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
    from ops_status.models import StatusSnapshot

    state = read_state(args.state)
    if state.get("outcome") == "unknown":
        raise RuntimeError("previous write result is unknown; inspect actual before continuing")
    snapshot = StatusSnapshot.model_validate_json(args.snapshot.read_text(encoding="utf-8"))
    if snapshot.source != "local-event-record":
        raise ValueError("only local-event-record snapshots can be published")
    observed_at = snapshot.observed_at.isoformat()
    previous_observation = state.get("last_attempted_observed_at")
    if previous_observation:
        previous_time = datetime.fromisoformat(previous_observation.replace("Z", "+00:00"))
        if snapshot.observed_at <= previous_time:
            return False

    token = token_provider(args.audience, args.ingest_client_id)
    attempt = {
        "last_attempted_observed_at": observed_at,
        "attempted_at": datetime.now(UTC).isoformat(),
        "outcome": "attempting",
    }
    write_state(args.state, attempt)
    try:
        response_status = sender(
            f"{args.base_url.rstrip('/')}/api/status",
            token,
            snapshot.model_dump_json().encode(),
        )
        if response_status != 204:
            raise RuntimeError(f"unexpected HTTP status {response_status}")
    except Exception:
        write_state(args.state, {**attempt, "outcome": "unknown"})
        raise
    write_state(args.state, {**attempt, "outcome": "confirmed"})
    return True


def main() -> int:
    args = parse_args()
    if not args.base_url.startswith("https://"):
        raise ValueError("base-url must use HTTPS")
    if args.interval < 1:
        raise ValueError("interval must be at least one second")
    while True:
        publish_if_new(args)
        if not args.watch:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
