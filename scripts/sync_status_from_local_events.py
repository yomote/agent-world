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
ACTIVITY_EVENTS = {"item_completed"}
CONFIG_KEYS = {"root_thread_id", "agents", "runtime_capacity"}
AGENT_KEYS = {
    "agent_path",
    "session_id",
    "parent_thread_id",
    "agent",
    "role",
    "task",
    "owner_label",
    "session_label",
    "task_label",
    "current_action",
    "progress_summary",
    "summary_updated_at",
    "issue_url",
    "pr_url",
    "next_action",
    "blocker",
}
PRIVATE_AGENT_KEYS = {"agent_path", "session_id", "parent_thread_id"}
REQUIRED_AGENT_KEYS = {"agent_path", "agent", "role", "task"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--output", type=Path, default=Path("artifacts/status/current.json"))
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument(
        "--compat-v1",
        action="store_true",
        help="write only fields accepted by an existing v1 Azure deployment",
    )
    return parser.parse_args()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def read_meta(path: Path) -> dict[str, Any] | None:
    with path.open(encoding="utf-8") as source:
        line = source.readline()
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if record.get("type") == "session_meta":
        payload = record.get("payload", {})
        return {
            "session_id": payload.get("id"),
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
                    timestamp = record.get("timestamp")
                    if record.get("type") == "session_meta" and isinstance(timestamp, str):
                        self.events[agent_path].append((parse_time(timestamp), "session_created"))
                        continue
                    if record.get("type") != "event_msg":
                        continue
                    payload = record.get("payload", {})
                    event_type = payload.get("type")
                    if event_type in TASK_EVENTS | ACTIVITY_EVENTS and isinstance(timestamp, str):
                        self.events[agent_path].append((parse_time(timestamp), event_type))
                self.offsets[agent_path] = source.tell()
        return self.events


def validate_config(data: Any) -> dict[str, Any]:
    if (
        not isinstance(data, dict)
        or not {"root_thread_id", "agents"} <= set(data)
        or not set(data).issubset(CONFIG_KEYS)
    ):
        raise ValueError(
            "config must contain root_thread_id, agents, and optional runtime_capacity"
        )
    if not isinstance(data["root_thread_id"], str) or not data["root_thread_id"]:
        raise ValueError("root_thread_id must be a non-empty string")
    if not isinstance(data["agents"], list) or not data["agents"]:
        raise ValueError("agents must be a non-empty list")
    if "runtime_capacity" in data and not isinstance(data["runtime_capacity"], dict):
        raise ValueError("runtime_capacity must be an object when supplied")
    paths = set()
    required = REQUIRED_AGENT_KEYS
    for agent in data["agents"]:
        if not isinstance(agent, dict) or not set(agent).issubset(AGENT_KEYS):
            raise ValueError("agent mapping contains unknown fields")
        if not required <= set(agent):
            raise ValueError("agent mapping is missing required fields")
        if agent["agent_path"] in paths:
            raise ValueError("agent_path mappings must be unique")
        paths.add(agent["agent_path"])
        exact_keys = {"session_id", "parent_thread_id"} & set(agent)
        if exact_keys and exact_keys != {"session_id", "parent_thread_id"}:
            raise ValueError("session_id and parent_thread_id must be configured together")
        for key in required | exact_keys:
            if not isinstance(agent[key], str) or not agent[key]:
                raise ValueError(f"{key} must be a non-empty string")
    return data


def find_sessions(
    sessions: Path, root_thread_id: str, mappings: dict[str, dict[str, Any]]
) -> dict[str, Path]:
    found: dict[str, list[Path]] = {path: [] for path in mappings}
    for path in sessions.rglob("*.jsonl"):
        meta = read_meta(path)
        if not meta:
            continue
        agent_path = meta["agent_path"]
        if agent_path not in found:
            continue
        expected = mappings[agent_path]
        expected_session = expected.get("session_id")
        expected_parent = expected.get("parent_thread_id", root_thread_id)
        if meta["parent_thread_id"] != expected_parent:
            continue
        if expected_session is not None and meta["session_id"] != expected_session:
            continue
        found[agent_path].append(path)
    invalid = {path: len(matches) for path, matches in found.items() if len(matches) != 1}
    if invalid:
        details = ", ".join(f"{path}={count}" for path, count in sorted(invalid.items()))
        raise ValueError(f"session_meta mapping is not unique: {details}")
    return {path: matches[0] for path, matches in found.items()}


def work_item(mapping: dict[str, Any], events: list[tuple[datetime, str]], now: datetime):
    public = {key: mapping[key] for key in AGENT_KEYS - PRIVATE_AGENT_KEYS if key in mapping}
    public.update(
        {
            "owner_label": mapping.get("owner_label", mapping["agent"]),
            "session_label": mapping.get("session_label", mapping["role"]),
            "task_label": mapping.get("task_label", mapping["task"]),
        }
    )
    task_events = [event for event in events if event[1] in TASK_EVENTS]
    latest_activity = max(events, key=lambda item: item[0]) if events else None
    if not task_events:
        if latest_activity is None:
            raise ValueError("matched session has no timestamped metadata")
        activity_at, _ = latest_activity
        stale = max(0, int((now - activity_at).total_seconds())) > ACTIVE_WINDOW_SECONDS
        return {
            **public,
            "status": "not-started",
            "observed_at": activity_at.isoformat(),
            "latest_activity": "session-created",
            "latest_activity_at": activity_at.isoformat(),
            "stale": stale,
            "note": "構造化task eventはまだ記録されていません。",
        }, activity_at
    observed_at, event_type = max(task_events, key=lambda item: item[0])
    activity_at, activity_type = latest_activity
    age = max(0, int((now - activity_at).total_seconds()))
    stale = age > ACTIVE_WINDOW_SECONDS
    if event_type == "task_complete":
        status = "stopped"
        stale = False
        note = "記録上、このturnは終了しています。課題全体の完了は示しません。"
    elif stale:
        status = "unknown"
        note = "開始後の終了記録を確認できず、構造化activityも途絶しています。"
    else:
        status = "running"
        note = "task開始後も構造化activityが継続しています。"
    activity_labels = {
        "session_created": "session-created",
        "task_started": "task-started",
        "task_complete": "task-complete",
        "item_completed": "structured-item",
    }
    return {
        **public,
        "status": status,
        "observed_at": observed_at.isoformat(),
        "latest_activity": activity_labels[activity_type],
        "latest_activity_at": activity_at.isoformat(),
        "stale": stale,
        "note": note,
    }, activity_at


def snapshot_payload(snapshot: Any, compat_v1: bool) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    if not compat_v1:
        return payload
    legacy_status = {
        "not-started": "unknown",
        "human-wait": "blocked",
        "stopped": "idle",
        "completed": "idle",
    }
    legacy_keys = {
        "agent",
        "role",
        "task",
        "status",
        "observed_at",
        "issue_url",
        "pr_url",
        "note",
    }
    payload["items"] = [
        {
            **{key: value for key, value in item.items() if key in legacy_keys},
            "status": legacy_status.get(item["status"], item["status"]),
        }
        for item in payload["items"]
    ]
    payload.pop("runtime_capacity", None)
    return payload


def write_snapshot(output: Path, payload: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
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

    snapshot = StatusSnapshot.model_validate(
        {
            "schema_version": 1,
            "source": "local-event-record",
            "observed_at": observed_at,
            "received_at": now,
            "items": items,
            "runtime_capacity": config.get("runtime_capacity"),
        }
    )
    payload = snapshot_payload(snapshot, args.compat_v1)
    if args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        previous.pop("received_at", None)
        comparable = {**payload}
        comparable.pop("received_at", None)
        if previous == comparable:
            print("status snapshot unchanged: no state or activity change")
            return False
    write_snapshot(args.output, payload)
    print(f"status snapshot written: source={snapshot.source} items={len(snapshot.items)}")
    return True


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
    args = parse_args()
    if args.interval < 1:
        raise ValueError("interval must be at least one second")
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    mappings = {agent["agent_path"]: agent for agent in config["agents"]}
    paths = find_sessions(args.sessions, config["root_thread_id"], mappings)
    reader = EventReader(paths)
    sync_once(args, config, reader)
    while args.watch:
        time.sleep(args.interval)
        sync_once(args, config, reader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
