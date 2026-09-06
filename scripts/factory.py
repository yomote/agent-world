"""既存の検査を実行し、成功・失敗・未完了を実測結果として残す。"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ["api:check", "lint", "format:check", "test", "build"]


def run_command(command: list[str], cwd: Path, log: Path, timeout: float = 300) -> dict:
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as output:
        try:
            child = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        except OSError as error:
            output.write(str(error))
            return {"status": "error", "exit_code": None, "seconds": 0}
        try:
            code = child.wait(timeout=timeout)
            status = "pass" if code == 0 else "fail"
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
            code, status = None, "timeout"
        return {
            "status": status,
            "exit_code": code,
            "seconds": round(time.monotonic() - started, 2),
        }


def write_report(report: dict, directory: Path) -> None:
    (directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows = [
        "# Factory check",
        "",
        f"開始 (UTC): {report['started_at']}",
        f"状態: {report['status']}",
        "",
        "| 検査 | 結果 | 秒 | ログ |",
        "| --- | --- | ---: | --- |",
    ]
    for item in report["checks"]:
        rows.append(
            f"| {item['script']} | {item['status']} | {item.get('seconds', '')} "
            f"| [{item['log']}]({item['log']}) |"
        )
    rows += [
        "",
        "この結果はローカルの上記検査だけを対象とします。GitHub設定の適用、独立レビュー、ブラウザの操作検証、リンク検査、IaC検査は別途確認が必要です。",
        "",
    ]
    (directory / "report.md").write_text("\n".join(rows), encoding="utf-8")


def run_checks(
    commands: list[tuple[str, list[str]]],
    cwd: Path,
    directory: Path,
    budget_seconds: float = 180,
) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + budget_seconds
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "checks": [
            {"script": name, "status": "not_run", "log": f"{index + 1}.log"}
            for index, (name, _) in enumerate(commands)
        ],
    }
    write_report(report, directory)
    for item, (_, command) in zip(report["checks"], commands, strict=True):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print("工場検査全体の期限です。残りはnot_runとして記録します。", flush=True)
            break
        item["status"] = "running"
        write_report(report, directory)
        item.update(run_command(command, cwd, directory / item["log"], timeout=remaining))
        write_report(report, directory)
        print(f"{item['script']}: {item['status']}", flush=True)
    report["status"] = "pass" if all(c["status"] == "pass" for c in report["checks"]) else "fail"
    write_report(report, directory)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    node, npm = os.environ.get("npm_node_execpath"), os.environ.get("npm_execpath")
    if not node or not npm:
        sys.exit("npm run factory:check から実行してください。")
    sys.exit(
        run_checks(
            [(script, [node, npm, "run", script]) for script in CHECKS],
            ROOT,
            ROOT / "artifacts/factory",
        )
    )
