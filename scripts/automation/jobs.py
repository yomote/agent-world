"""公開Codex CLIを制限時間内に起動し、構造化eventを生存中に記録する。"""

import hashlib
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
RUNTIME_PROFILE = "native-local-v1"


def native_node():
    return (
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    )


def prepare_runtime(task, workspace):
    """既存依存だけを用意し、child予約前に固定commandを短時間で検査する。"""
    task.boundary()
    prettier = workspace / "node_modules/prettier"
    if prettier.exists():
        raise Stop("stopped", "runtime_destination_exists")
    source = task.root / "node_modules/prettier"
    # 別ownerのpathへリンク経由で書かず、downloadやpackage managerを起動しない。
    for directory in (source, workspace / ".venv"):
        for path in [directory, *directory.rglob("*")]:
            task.heartbeat()
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise Stop("stopped", "runtime_link_not_allowed")

    def copy_file(source_path, target_path):
        task.heartbeat()
        result = shutil.copy2(source_path, target_path)
        task.boundary()
        return result

    shutil.copytree(source, prettier, copy_function=copy_file)
    commands = {
        "node": [str(native_node()), "--version"],
        "python": [str(workspace / ".venv/Scripts/python.exe"), "--version"],
        "pytest": [str(workspace / ".venv/Scripts/python.exe"), "-m", "pytest", "--version"],
        "ruff": [str(workspace / ".venv/Scripts/python.exe"), "-m", "ruff", "--version"],
        "prettier": [str(native_node()), str(prettier / "bin/prettier.cjs"), "--version"],
    }
    versions = {}
    for name, command in commands.items():
        task.heartbeat()
        try:
            result = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                timeout=min(10, max(0.01, task.monotonic_deadline - time.monotonic())),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Stop("stopped", "runtime_preflight_failed") from None
        if result.returncode or not result.stdout.strip() or len(result.stdout) > 256:
            raise Stop("stopped", "runtime_preflight_failed")
        versions[name] = result.stdout.decode("utf-8").strip()
    task.boundary()
    profile = {
        "kind": RUNTIME_PROFILE,
        "node": str(native_node()),
        "python": str(workspace / ".venv/Scripts/python.exe"),
        "prettier": str(prettier / "bin/prettier.cjs"),
        "versions": versions,
        "boundary": "driver_host_only_not_child_sandbox",
    }
    data = task.data()
    data["runtime_profile"] = profile
    task.save_event(data, "runtime_preflight_verified")
    return profile


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
    output_path = task.artifact_directory / f"cli-{data['cli_starts']}.jsonl"
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
        schema_path = task.artifact_directory / "job-output-schema.json"
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
    command_failed = False
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
                elif kind in ("policy.denied", "approval.denied"):
                    raise Stop("failed", "cli_policy_denied")
                elif kind in ("turn.failed", "error"):
                    raise Stop("unknown", "cli_error_no_retry")
                elif kind == "item.completed":
                    item = event.get("item", {})
                    if isinstance(item, dict) and item.get("type") == "agent_message":
                        last_message = item.get("text")
                    if isinstance(item, dict) and item.get("type") == "command_execution":
                        if item.get("exit_code") not in (0, None):
                            command_failed = True
                elif kind == "turn.completed":
                    completed = True
            else:
                raise Stop("unknown", "cli_timeout_no_retry")
        code = child.wait(timeout=max(0.01, min(10, end - time.monotonic())))
        if owner is None:
            raise Stop("unknown", "cli_start_event_missing")
        if not completed:
            raise Stop("unknown", "cli_completion_event_missing")
        if code != 0:
            raise Stop("unknown", "cli_exit_not_success")
        data = task.data()
        data["cli_exit_code"] = code
        data["cli_pid"] = None
        data["cli_completion"] = {
            "owner": owner,
            "exit_code": code,
            "turn_completed": True,
            "command_failed": command_failed,
            "log": str(output_path.relative_to(task.root)),
            "log_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            "message_sha256": hashlib.sha256(str(last_message).encode()).hexdigest(),
        }
        task.save_event(data, "cli_completed")
        return {"owner": owner, "message": last_message, "exit_code": code}
    finally:
        stop_process(child)
