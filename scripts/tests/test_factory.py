"""検査の失敗を緑にせず、後続の検査と証拠を残すことを検証する。"""

import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("factory", Path(__file__).parents[1] / "factory.py")
factory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factory)


def test_failure_does_not_hide_later_results_or_reuse_old_success(tmp_path):
    commands = [
        ("broken", [sys.executable, "-c", "print('failure evidence'); exit(4)"]),
        ("healthy", [sys.executable, "-c", "print('later check ran')"]),
    ]
    assert factory.run_checks(commands, tmp_path, tmp_path) == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert [c["status"] for c in report["checks"]] == ["fail", "pass"]
    assert report["checks"][0]["exit_code"] == 4
    assert "later check ran" in (tmp_path / "2.log").read_text()
    assert "failure evidence" in (tmp_path / "1.log").read_text()
    assert (
        factory.run_checks([("missing", [str(tmp_path / "missing-tool")])], tmp_path, tmp_path) == 1
    )
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["checks"]) == 1
    assert report["checks"][0]["status"] == "error"


def test_hanging_command_is_timeout_not_success(tmp_path):
    result = factory.run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        tmp_path / "timeout.log",
        0.2,
    )
    assert result["status"] == "timeout"
    assert result["exit_code"] is None


def test_total_deadline_preserves_report_before_ci_timeout(tmp_path):
    """1つのハングで全体予算を使った後、CI期限まで次のハングを待たない。"""
    commands = [
        ("hang", [sys.executable, "-c", "import time; time.sleep(30)"]),
        ("later", [sys.executable, "-c", "raise AssertionError('must not run')"]),
    ]
    assert factory.run_checks(commands, tmp_path, tmp_path, budget_seconds=0.2) == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "fail"
    assert [c["status"] for c in report["checks"]] == ["timeout", "not_run"]
    assert not (tmp_path / "2.log").exists()
