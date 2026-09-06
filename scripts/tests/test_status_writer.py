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
