import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parents[2]


def record(
    path: Path,
    agent_path: str,
    events: list[tuple[str, str]],
    session_id: str = "session-1",
) -> None:
    rows = [
        {
            "timestamp": "2026-09-06T12:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "parent_thread_id": "root-1",
                "agent_path": agent_path,
                "secret": "must-not-leak",
            },
        }
    ]
    rows.extend(
        {"timestamp": timestamp, "type": "event_msg", "payload": {"type": kind}}
        for timestamp, kind in events
    )
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def config(path: Path, runtime_capacity: dict | None = None) -> None:
    payload = {
        "root_thread_id": "root-1",
        "agents": [
            {
                "agent_path": "/root/worker",
                "agent": "status owner",
                "role": "実装担当",
                "task": "Issue #17",
                "current_action": "表示の検証中",
                "progress_summary": "実装済み、レビュー待ち",
                "summary_updated_at": "2026-09-06T12:00:00Z",
                "issue_url": "https://github.com/yomote/agent-world/issues/17",
            }
        ],
    }
    if runtime_capacity is not None:
        payload["runtime_capacity"] = runtime_capacity
    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def run(config_path: Path, sessions: Path, output: Path, compat_v1: bool = False):
    command = [
        sys.executable,
        str(ROOT / "scripts" / "sync_status_from_local_events.py"),
        "--config",
        str(config_path),
        "--sessions",
        str(sessions),
        "--output",
        str(output),
    ]
    if compat_v1:
        command.append("--compat-v1")
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_sync_emits_only_sanitized_idle_state_and_does_not_refresh_old_record(tmp_path):
    """本文・path・IDを漏らさず、同じ記録の再読でfresh扱いに戻す回帰を防ぐ。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    record(
        sessions / "worker.jsonl",
        "/root/worker",
        [
            ("2026-09-06T12:00:01Z", "task_started"),
            ("2026-09-06T12:00:02Z", "task_complete"),
        ],
    )
    config_path = tmp_path / "config.json"
    config(config_path)
    output = tmp_path / "status.json"

    first = run(config_path, sessions, output)
    assert first.returncode == 0, first.stderr
    original = output.read_text(encoding="utf-8")
    payload = json.loads(original)
    assert payload["source"] == "local-event-record"
    assert payload["items"][0]["status"] == "stopped"
    assert payload["items"][0]["current_action"] == "表示の検証中"
    assert payload["items"][0]["progress_summary"] == "実装済み、レビュー待ち"
    assert payload["items"][0]["summary_updated_at"] == "2026-09-06T12:00:00Z"
    assert payload["items"][0]["stale"] is False
    assert "agent_path" not in original
    assert "root-1" not in original
    assert "must-not-leak" not in original

    second = run(config_path, sessions, output)
    assert second.returncode == 0
    assert "unchanged" in second.stdout
    assert output.read_text(encoding="utf-8") == original


def test_sync_stops_when_session_mapping_is_ambiguous(tmp_path):
    """同じagent_pathを推測で別sessionへ結び付ける回帰を防ぐ。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for name in ("one", "two"):
        record(
            sessions / f"{name}.jsonl",
            "/root/worker",
            [("2026-09-06T12:00:01Z", "task_started")],
        )
    config_path = tmp_path / "config.json"
    config(config_path)
    output = tmp_path / "status.json"

    result = run(config_path, sessions, output)
    assert result.returncode != 0
    assert "not unique" in result.stderr
    assert not output.exists()


def test_sync_does_not_leave_old_unfinished_turn_running(tmp_path):
    """終了記録が欠けた古いturnを無期限にrunning表示する回帰を防ぐ。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    record(
        sessions / "worker.jsonl",
        "/root/worker",
        [("2020-01-01T00:00:00Z", "task_started")],
    )
    config_path = tmp_path / "config.json"
    config(config_path)
    output = tmp_path / "status.json"

    result = run(config_path, sessions, output)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["items"][0]["status"] == "unknown"


def test_sync_ignores_task_name_in_non_event_record(tmp_path):
    """別種record内の同名payloadをtask eventと誤認する回帰を防ぐ。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "worker.jsonl"
    record(path, "/root/worker", [("2020-01-01T00:00:00Z", "task_started")])
    unrelated = {
        "timestamp": "2026-09-06T12:00:02Z",
        "type": "response_item",
        "payload": {"type": "task_complete"},
    }
    with path.open("a", encoding="utf-8") as target:
        target.write("\n" + json.dumps(unrelated))
    config_path = tmp_path / "config.json"
    config(config_path)
    output = tmp_path / "status.json"

    result = run(config_path, sessions, output)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["items"][0]["status"] == "unknown"


def test_sync_uses_sanitized_activity_envelope_for_a_long_running_turn(tmp_path):
    """長いturnを開始時刻だけでunknownにし、本文やtool結果を送る回帰を防ぐ。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = datetime.now(UTC).isoformat()
    record(
        sessions / "worker.jsonl",
        "/root/worker",
        [("2020-01-01T00:00:00Z", "task_started"), (current, "item_completed")],
    )
    config_path = tmp_path / "config.json"
    config(config_path)
    output = tmp_path / "status.json"

    result = run(config_path, sessions, output)

    assert result.returncode == 0, result.stderr
    item = json.loads(output.read_text(encoding="utf-8"))["items"][0]
    assert item["status"] == "running"
    assert item["latest_activity"] == "structured-item"
    assert item["stale"] is False


def test_sync_requires_exact_allowlisted_session_metadata(tmp_path):
    """同じagent pathの別sessionを現在対象へ取り違える回帰を防ぐ。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    record(
        sessions / "worker.jsonl",
        "/root/worker",
        [("2026-09-06T12:00:01Z", "task_started")],
        session_id="actual-session",
    )
    config_path = tmp_path / "config.json"
    config(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["agents"][0].update({"session_id": "expected-session", "parent_thread_id": "root-1"})
    config_path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "status.json"

    result = run(config_path, sessions, output)

    assert result.returncode != 0
    assert "not unique" in result.stderr
    assert not output.exists()


def test_sync_can_seed_an_existing_v1_azure_api(tmp_path):
    """schema更新前の公開APIが追加fieldで初回snapshotを拒否する回帰を防ぐ。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = datetime.now(UTC).isoformat()
    record(
        sessions / "worker.jsonl",
        "/root/worker",
        [(current, "task_started"), (current, "item_completed")],
    )
    config_path = tmp_path / "config.json"
    config(config_path)
    output = tmp_path / "status.json"

    result = run(config_path, sessions, output, compat_v1=True)

    assert result.returncode == 0, result.stderr
    item = json.loads(output.read_text(encoding="utf-8"))["items"][0]
    assert item["status"] == "running"
    assert set(item) == {
        "agent",
        "role",
        "task",
        "status",
        "observed_at",
        "issue_url",
        "pr_url",
        "note",
    }


def test_sync_keeps_explicit_runtime_capacity_observation_without_retimestamping(tmp_path):
    """activity更新時に別sourceのcapacity観測時刻を現在へ偽装する回帰を防ぐ。"""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = datetime.now(UTC).isoformat()
    record(sessions / "worker.jsonl", "/root/worker", [(current, "task_started")])
    config_path = tmp_path / "config.json"
    capacity_observed_at = "2026-09-12T16:55:23.9476494Z"
    config(
        config_path,
        {
            "scope": "/root session tree",
            "observed_at": capacity_observed_at,
            "state_source": "runtime-list-agents-metadata",
            "limit_source": "runtime-instructions",
            "running": 4,
            "idle": 0,
            "completed": 2,
            "total": 6,
            "max_concurrent_agents": 8,
        },
    )
    output = tmp_path / "status.json"

    result = run(config_path, sessions, output)

    assert result.returncode == 0, result.stderr
    capacity = json.loads(output.read_text(encoding="utf-8"))["runtime_capacity"]
    # Pythonのdatetimeはmicrosecondsまでなので、7桁目は保存時に正規化される。
    assert capacity["observed_at"] == "2026-09-12T16:55:23.947649Z"
    assert capacity["running"] == 4
    assert capacity["max_concurrent_agents"] == 8
