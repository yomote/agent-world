"""公開Codex CLIを制限時間内に起動し、構造化eventを生存中に記録する。"""

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from .evidence import thread_id
from .transport import Stop

MAX_LOG_BYTES = 8 * 1024 * 1024


def stop_process(child):
    if child.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        os.killpg(child.pid, signal.SIGKILL)
    child.wait(timeout=10)


def run_job(task, workspace: Path, prompt: str, *, seconds=900, read_only=False, schema=None):
    task.boundary()
    data = task.data()
    if data["cli_starts"] >= data["max_cli_starts"]:
        raise Stop("stopped", "cli_start_budget")
    executable = shutil.which("codex")
    if executable is None:
        raise Stop("stopped", "codex_unavailable")
    data["cli_starts"] += 1
    task.save_event(data, "cli_reserved")
    output_path = task.directory / f"cli-{data['cli_starts']}.jsonl"
    arguments = [
        executable,
        "-a",
        "on-request",
        "exec",
        "--json",
        "--color",
        "never",
        "--sandbox",
        "read-only" if read_only else "workspace-write",
        "-C",
        str(workspace),
        "-",
    ]
    if schema is not None:
        schema_path = task.directory / "job-output-schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        arguments[-1:-1] = ["--output-schema", str(schema_path)]
    child = subprocess.Popen(
        arguments,
        cwd=workspace,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    data = task.data()
    data["cli_pid"] = child.pid
    task.save_event(data, "cli_started")
    try:
        child.stdin.write(prompt.encode("utf-8"))
        child.stdin.close()
    except OSError:
        stop_process(child)
        raise
    lines = queue.Queue(maxsize=256)

    def read_lines():
        while line := child.stdout.readline(MAX_LOG_BYTES + 1):
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()
    end = min(time.monotonic() + seconds, task.monotonic_deadline)
    consumed, completed, owner, last_message = 0, False, None, None
    try:
        with output_path.open("wb") as output:
            while time.monotonic() < end:
                task.heartbeat()
                try:
                    line = lines.get(timeout=0.25)
                except queue.Empty:
                    continue
                if line is None:
                    break
                consumed += len(line)
                if consumed > MAX_LOG_BYTES:
                    raise Stop("unknown", "cli_output_limit")
                output.write(line)
                output.flush()
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind == "thread.started":
                    owner = thread_id(event["thread_id"])
                    data = task.data()
                    data["job_owner"] = owner
                    task.save_event(data, "job_owner_observed")
                elif kind in ("approval_requested", "approval.required"):
                    raise Stop("approval_wait", "cli_approval_required")
                elif kind in ("turn.failed", "error"):
                    raise Stop("unknown", "cli_error_no_retry")
                elif kind == "item.completed":
                    item = event.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "agent_message":
                        last_message = item.get("text")
                elif kind == "turn.completed":
                    completed = True
            else:
                raise Stop("unknown", "cli_timeout_no_retry")
        code = child.wait(timeout=max(0.01, min(10, end - time.monotonic())))
        if code != 0 or not completed or owner is None:
            raise Stop("unknown", "cli_result_incomplete")
        data = task.data()
        data["cli_exit_code"] = code
        data["cli_pid"] = None
        task.save_event(data, "cli_completed")
        return {"owner": owner, "message": last_message, "exit_code": code}
    finally:
        stop_process(child)
