import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def record(path: Path, agent_path: str, events: list[tuple[str, str]]) -> None:
    rows = [
        {
            "timestamp": "2026-09-06T12:00:00Z",
            "type": "session_meta",
            "payload": {
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


def config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "root_thread_id": "root-1",
                "agents": [
                    {
                        "agent_path": "/root/worker",
                        "agent": "status owner",
                        "role": "実装担当",
                        "task": "Issue #17",
                        "issue_url": "https://github.com/yomote/agent-world/issues/17",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def run(config_path: Path, sessions: Path, output: Path):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "sync_status_from_local_events.py"),
            "--config",
            str(config_path),
            "--sessions",
            str(sessions),
            "--output",
            str(output),
        ],
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
    assert payload["items"][0]["status"] == "idle"
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
