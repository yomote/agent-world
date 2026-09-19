import json
import subprocess
import sys
from pathlib import Path


def test_manual_writer_rejects_codex_event_source(tmp_path):
    """実adapter未接続の手動入力をCodex live eventとして表示する回帰を防ぐ。"""
    root = Path(__file__).parents[2]
    output = tmp_path / "snapshot.json"

    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "write_status_snapshot.py"),
            "--source",
            "codex-event",
            "--observed-at",
            "2026-09-06T12:45:04Z",
            "--output",
            str(output),
        ],
        cwd=root,
        input="[]",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid choice" in result.stderr
    assert not output.exists()


def test_manual_writer_rejects_local_event_record_source(tmp_path):
    """手入力をlocal event readerの観測結果として偽装する回帰を防ぐ。"""
    root = Path(__file__).parents[2]
    output = tmp_path / "snapshot.json"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "write_status_snapshot.py"),
            "--source",
            "local-event-record",
            "--observed-at",
            "2026-09-06T12:45:04Z",
            "--output",
            str(output),
        ],
        cwd=root,
        input="[]",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not output.exists()


def test_manual_writer_persists_typed_pm_task_projection_without_registry_claim(tmp_path):
    """PM観測cacheの保存に架空Front Desk claimを要求・生成しない。"""
    root = Path(__file__).parents[2]
    output = tmp_path / "snapshot.json"
    projection = tmp_path / "pm-tasks.json"
    comment = tmp_path / "pm-tasks.md"
    projection.write_text(
        json.dumps(
            {
                "source_kind": "pm-observation",
                "source_version": "issue-45-pm-packet-v1",
                "source_refs": ["https://github.com/yomote/agent-world/issues/45"],
                "observed_at": "2026-09-19T06:30:00Z",
                "tasks": [
                    {
                        "task_id": "pr-92-continuity",
                        "title": "closure guard",
                        "purpose": "未解決taskを次手へ接続する",
                        "acceptance_summary": "current mainで再検証する",
                        "owner": "governance-continuity",
                        "state": "running",
                        "current_step": "UI検証",
                        "next_action": "独立review",
                        "resume_trigger": None,
                        "blocker": None,
                        "waiting_on": "none",
                        "waiting_detail": None,
                        "issue_url": "https://github.com/yomote/agent-world/issues/45",
                        "pr_url": "https://github.com/yomote/agent-world/pull/92",
                        "source_version": "pm-packet-2026-09-19T06:30:00Z",
                        "observed_at": "2026-09-19T06:30:00Z",
                        "po_status": "pending",
                        "evidence": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "write_status_snapshot.py"),
            "--source",
            "pm-confirmed",
            "--observed-at",
            "2026-09-19T06:30:00Z",
            "--pm-task-projection",
            str(projection),
            "--output",
            str(output),
            "--pm-task-comment-output",
            str(comment),
        ],
        cwd=root,
        input="[]",
        capture_output=True,
        text=True,
        check=False,
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert result.returncode == 0
    assert saved["pm_task_projection"]["tasks"][0]["task_id"] == "pr-92-continuity"
    assert saved["pm_task_projection"]["content_digest"].startswith("sha256:")
    assert saved.get("request_registry") is None
    rendered = comment.read_text(encoding="utf-8")
    assert saved["pm_task_projection"]["content_digest"] in rendered
    assert "pr-92-continuity" in rendered

    changed = json.loads(projection.read_text(encoding="utf-8"))
    changed["tasks"][0]["owner"] = "another-owner"
    changed["observed_at"] = "2026-09-19T06:31:00Z"
    projection.write_text(json.dumps(changed), encoding="utf-8")
    rejected = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "write_status_snapshot.py"),
            "--source",
            "pm-confirmed",
            "--observed-at",
            "2026-09-19T06:31:00Z",
            "--pm-task-projection",
            str(projection),
            "--output",
            str(output),
        ],
        cwd=root,
        input="[]",
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "PM task cannot change at the same observation" in rejected.stderr
