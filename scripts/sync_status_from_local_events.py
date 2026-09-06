"""構造化されたlocal Codex event記録だけから管理statusを更新する。"""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACTIVE_WINDOW_SECONDS = 120
TASK_EVENTS = {"task_started", "task_complete"}
CONFIG_KEYS = {"root_thread_id", "agents"}
AGENT_KEYS = {"agent_path", "agent", "role", "task", "issue_url", "pr_url"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--output", type=Path, default=Path("artifacts/status/current.json"))
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    return parser.parse_args()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def read_meta(path: Path) -> dict[str, Any] | None:
    with path.open(encoding="utf-8") as source:
        for _ in range(2):
            line = source.readline()
            if not line:
                break
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if record.get("type") == "session_meta":
                payload = record.get("payload", {})
                return {
                    "parent_thread_id": payload.get("parent_thread_id"),
                    "agent_path": payload.get("agent_path"),
                }
    return None


class EventReader:
    def __init__(self, paths: dict[str, Path]):
        self.paths = paths
        self.offsets = {agent_path: 0 for agent_path in paths}
        self.events: dict[str, list[tuple[datetime, str]]] = {
            agent_path: [] for agent_path in paths
        }

    def poll(self) -> dict[str, list[tuple[datetime, str]]]:
        for agent_path, path in self.paths.items():
            if path.stat().st_size < self.offsets[agent_path]:
                self.offsets[agent_path] = 0
                self.events[agent_path] = []
            with path.open("rb") as source:
                source.seek(self.offsets[agent_path])
                while True:
                    line_start = source.tell()
                    line = source.readline()
                    if not line:
                        break
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        if not line.endswith(b"\n"):
                            source.seek(line_start)
                            break
                        continue
                    if record.get("type") != "event_msg":
                        continue
                    payload = record.get("payload", {})
                    event_type = payload.get("type")
                    timestamp = record.get("timestamp")
                    if event_type in TASK_EVENTS and isinstance(timestamp, str):
                        self.events[agent_path].append((parse_time(timestamp), event_type))
                self.offsets[agent_path] = source.tell()
        return self.events


def validate_config(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != CONFIG_KEYS:
        raise ValueError("config must contain only root_thread_id and agents")
    if not isinstance(data["root_thread_id"], str) or not data["root_thread_id"]:
        raise ValueError("root_thread_id must be a non-empty string")
    if not isinstance(data["agents"], list) or not data["agents"]:
        raise ValueError("agents must be a non-empty list")
    paths = set()
    required = AGENT_KEYS - {"issue_url", "pr_url"}
    for agent in data["agents"]:
        if not isinstance(agent, dict) or not set(agent).issubset(AGENT_KEYS):
            raise ValueError("agent mapping contains unknown fields")
        if not required <= set(agent):
            raise ValueError("agent mapping is missing required fields")
        if agent["agent_path"] in paths:
            raise ValueError("agent_path mappings must be unique")
        paths.add(agent["agent_path"])
    return data


def find_sessions(sessions: Path, root_thread_id: str, wanted_paths: set[str]) -> dict[str, Path]:
    found: dict[str, list[Path]] = {path: [] for path in wanted_paths}
    for path in sessions.rglob("*.jsonl"):
        meta = read_meta(path)
        if not meta or meta["parent_thread_id"] != root_thread_id:
            continue
        agent_path = meta["agent_path"]
        if agent_path in found:
            found[agent_path].append(path)
    invalid = {path: len(matches) for path, matches in found.items() if len(matches) != 1}
    if invalid:
        details = ", ".join(f"{path}={count}" for path, count in sorted(invalid.items()))
        raise ValueError(f"session_meta mapping is not unique: {details}")
    return {path: matches[0] for path, matches in found.items()}


def work_item(mapping: dict[str, Any], events: list[tuple[datetime, str]], now: datetime):
    public = {key: mapping[key] for key in AGENT_KEYS - {"agent_path"} if key in mapping}
    if not events:
        return {
            **public,
            "status": "unknown",
            "observed_at": now.isoformat(),
            "note": "構造化task eventを確認できません。",
        }, None
    observed_at, event_type = max(events, key=lambda item: item[0])
    age = max(0, int((now - observed_at).total_seconds()))
    if event_type == "task_complete":
        status = "idle"
        note = "記録上、このturnは終了しています。課題全体の完了は示しません。"
    elif age > ACTIVE_WINDOW_SECONDS:
        status = "unknown"
        note = "開始後の終了記録を確認できず、記録更新も停止しています。"
    else:
        status = "running"
        note = "local event記録上、このturnを実行中です。"
    return {
        **public,
        "status": status,
        "observed_at": observed_at.isoformat(),
        "note": note,
    }, observed_at


def write_snapshot(output: Path, snapshot: Any) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.{os.getpid()}.tmp")
    temporary.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(output)


def sync_once(args: argparse.Namespace, config: dict[str, Any], reader: EventReader) -> bool:
    from ops_status.models import StatusSnapshot

    mappings = {agent["agent_path"]: agent for agent in config["agents"]}
    events_by_agent = reader.poll()
    now = datetime.now(UTC)
    items = []
    event_times = []
    for agent_path, mapping in mappings.items():
        item, event_time = work_item(mapping, events_by_agent[agent_path], now)
        items.append(item)
        if event_time:
            event_times.append(event_time)
    if not event_times:
        raise ValueError("no structured task event found")
    observed_at = max(event_times)

    if args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        if (
            previous.get("source") == "local-event-record"
            and parse_time(previous["observed_at"]) >= observed_at
        ):
            print("status snapshot unchanged: no new structured event")
            return False

    snapshot = StatusSnapshot.model_validate(
        {
            "schema_version": 1,
            "source": "local-event-record",
            "observed_at": observed_at,
            "received_at": now,
            "items": items,
        }
    )
    write_snapshot(args.output, snapshot)
    print(f"status snapshot written: source={snapshot.source} items={len(snapshot.items)}")
    return True


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
    args = parse_args()
    if args.interval < 1:
        raise ValueError("interval must be at least one second")
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    mappings = {agent["agent_path"]: agent for agent in config["agents"]}
    paths = find_sessions(args.sessions, config["root_thread_id"], set(mappings))
    reader = EventReader(paths)
    sync_once(args, config, reader)
    while args.watch:
        time.sleep(args.interval)
        sync_once(args, config, reader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
