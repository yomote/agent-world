"""固定recipeを一件ずつ実行する、永続化されたbounded local dispatcher。"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

if __package__:
    from .evidence import observe, thread_id
else:
    from evidence import observe, thread_id

ROOT = Path(__file__).resolve().parents[2]
STATE = "artifacts/self-improvement"
TARGET = "AGENTS.md"
RUNBOOK = "docs/runbooks/self-improvement.md"
RECIPE = "agents-self-improvement-entry-v1"
ENTRY = (
    "- bounded local改善の明示起動と停止・回復は"
    "[自律改善runner](docs/runbooks/self-improvement.md)に従う。"
)
MAX_FILE_BYTES = 128 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    """固定pathにもsymlink/junction/hardlinkで別所有物を接続させない。"""
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
                or (path.is_file() and info.st_nlink != 1)
            ):
                raise ValueError("linked_path_rejected")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path_outside_checkout")
    return path


def read_input(root: Path, relative: str) -> bytes:
    path = safe_path(root, relative)
    if not path.is_file():
        raise ValueError("input_missing")
    with path.open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("input_limit")
    data.decode("utf-8")
    return data


def replacement(before: bytes) -> bytes:
    newline = b"\r\n" if b"\r\n" in before else b"\n"
    return before.rstrip(b"\r\n") + newline * 2 + ENTRY.encode() + newline


def clean_target(root: Path, timeout: float = 5) -> bool:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            TARGET,
        ],
        cwd=root,
        capture_output=True,
        timeout=timeout,
        check=True,
    )
    return not result.stdout


@contextmanager
def dispatcher(root: Path):
    """全mutationは同じ固定OS lockを保持する。lease期限だけで並行writerを作らない。"""
    directory = safe_path(root, STATE)
    directory.mkdir(parents=True, exist_ok=True)
    lock = safe_path(root, STATE + "/dispatcher.lock")
    with lock.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("dispatcher_busy") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Runner:
    """呼出し元は寿命全体でdispatcher lockを保持する。"""

    def __init__(self, root: Path):
        self.root = root.resolve()
        for suffix in ("", "-journal", "-wal", "-shm"):
            safe_path(self.root, STATE + "/state.sqlite3" + suffix)
        self.db = sqlite3.connect(safe_path(self.root, STATE + "/state.sqlite3"), timeout=0)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT);
            CREATE TABLE IF NOT EXISTS candidates (
                id TEXT PRIMARY KEY, packet TEXT NOT NULL, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, owner TEXT, token TEXT,
                lease_until REAL, reason TEXT NOT NULL, verification TEXT NOT NULL DEFAULT 'not_run'
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, at REAL NOT NULL, candidate TEXT, kind TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observation (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT);
            """
        )

    def close(self):
        self.db.close()

    def config(self) -> dict:
        row = self.db.execute("SELECT data FROM config WHERE id=1").fetchone()
        if row is None:
            raise ValueError("init_required")
        config = json.loads(row[0])
        if config["version"] != 1 or config["root"] != str(self.root):
            raise ValueError("state_identity_mismatch")
        return config

    def save_config(self, config: dict):
        self.db.execute("INSERT OR REPLACE INTO config VALUES (1, ?)", (json.dumps(config),))

    def event(self, candidate: str | None, kind: str):
        self.db.execute(
            "INSERT INTO events(at,candidate,kind) VALUES (?,?,?)", (time.time(), candidate, kind)
        )

    def init(self, attempts: int, seconds: float, api_calls: int = 0, cost: int = 0):
        if self.db.execute("SELECT 1 FROM config").fetchone():
            raise ValueError("already_initialized_no_budget_reset")
        if not 1 <= attempts <= 3 or not math.isfinite(seconds) or not 1 <= seconds <= 300:
            raise ValueError("budget_out_of_range")
        if api_calls != 0 or cost != 0:
            raise ValueError("external_budget_must_be_zero")
        now = time.time()
        with self.db:
            self.save_config(
                dict(
                    version=1,
                    root=str(self.root),
                    max_attempts=attempts,
                    attempts=0,
                    deadline=now + seconds,
                    last_seen=now,
                    stopped=None,
                    api_calls=0,
                    cost_microusd=0,
                )
            )
            self.event(None, "initialized")

    def boundary(self) -> str | None:
        config = self.config()
        now = time.time()
        reason = config["stopped"]
        if now < config["last_seen"]:
            reason = "clock_rollback"
        elif now >= config["deadline"]:
            reason = "time_budget"
        elif config["attempts"] >= config["max_attempts"]:
            reason = "attempt_budget"
        config["last_seen"] = max(now, config["last_seen"])
        config["stopped"] = reason
        with self.db:
            self.save_config(config)
        return reason

    def scan(self):
        reason = self.boundary()
        if reason:
            return {"state": "stopped", "reason": reason}
        existing = self.db.execute(
            "SELECT * FROM candidates ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if existing:
            return dict(existing)
        before = read_input(self.root, TARGET)
        runbook = read_input(self.root, RUNBOOK)
        if RUNBOOK.encode() in before:
            return {"state": "no_candidate", "reason": "entry_present"}
        packet = {
            "recipe": RECIPE,
            "source": "allowlisted-repository-check",
            "scope": [TARGET],
            "evidence": {TARGET: digest(before), RUNBOOK: digest(runbook)},
            "expected_after": digest(replacement(before)),
            "finding": "self_improvement_entry_missing",
            "check": "exact_append_and_runbook_digest",
        }
        candidate = digest(json.dumps(packet, sort_keys=True).encode())
        remaining = self.config()["deadline"] - time.time()
        state = (
            "ready" if clean_target(self.root, max(0.001, min(5, remaining))) else "approval_wait"
        )
        reason = self.boundary()
        if reason:
            return {"state": "stopped", "reason": reason}
        with self.db:
            self.db.execute(
                "INSERT INTO candidates(id,packet,state,reason) VALUES (?,?,?,?)",
                (
                    candidate,
                    json.dumps(packet),
                    state,
                    "candidate_created" if state == "ready" else "target_dirty",
                ),
            )
            self.event(candidate, state)
        return self.candidate(candidate)

    def candidate(self, candidate: str) -> dict:
        row = self.db.execute("SELECT * FROM candidates WHERE id=?", (candidate,)).fetchone()
        if row is None:
            raise ValueError("candidate_missing")
        return dict(row)

    def transition(self, candidate: str, state: str, reason: str, verification="not_run"):
        with self.db:
            self.db.execute(
                "UPDATE candidates SET state=?,reason=?,verification=?,token=NULL,lease_until=NULL "
                "WHERE id=?",
                (state, reason, verification, candidate),
            )
            self.event(candidate, reason)
        return self.candidate(candidate)

    def hold(self, candidate: str):
        row = self.candidate(candidate)
        if row["state"] != "ready":
            return row
        return self.transition(candidate, "approval_wait", "human_decision_required")

    def recover(self, candidate: str):
        row = self.candidate(candidate)
        if row["state"] != "running":
            return row
        if time.time() < row["lease_until"]:
            return {**row, "reason": "owner_lease_active"}
        return self.transition(candidate, "unknown", "expired_lease_no_replay", "unknown")

    def run(self, candidate: str, owner: str):
        owner = thread_id(owner)
        row = self.candidate(candidate)
        if row["state"] == "running":
            return self.recover(candidate)
        if row["state"] != "ready":
            return row
        reason = self.boundary()
        if reason:
            return self.transition(candidate, "stopped", reason)
        config = self.config()
        token = str(uuid4())
        config["attempts"] += 1
        lease_until = min(time.time() + 30, config["deadline"])
        with self.db:
            self.save_config(config)
            self.db.execute(
                "UPDATE candidates SET state='running',attempts=attempts+1,owner=?,token=?,"
                "lease_until=?,reason='reserved' WHERE id=?",
                (owner, token, lease_until, candidate),
            )
            self.event(candidate, "reserved")
        remaining = lease_until - time.time()
        end = time.monotonic() + max(0, remaining)

        def fence():
            current = self.candidate(candidate)
            if current["token"] != token or time.time() >= lease_until or time.monotonic() >= end:
                raise TimeoutError("lease_or_time_budget")

        try:
            fence()
            packet = json.loads(row["packet"])
            before = read_input(self.root, TARGET)
            runbook = read_input(self.root, RUNBOOK)
            # 保存packetも命令ではない。固定recipeと元hashを毎回照合する。
            expected = {
                "recipe": RECIPE,
                "source": "allowlisted-repository-check",
                "scope": [TARGET],
                "evidence": {TARGET: digest(before), RUNBOOK: digest(runbook)},
                "expected_after": digest(replacement(before)),
                "finding": "self_improvement_entry_missing",
                "check": "exact_append_and_runbook_digest",
            }
            if packet != expected or candidate != digest(
                json.dumps(packet, sort_keys=True).encode()
            ):
                return self.transition(candidate, "stopped", "evidence_changed")
            if not clean_target(self.root, max(0.001, min(5, end - time.monotonic()))):
                return self.transition(candidate, "approval_wait", "target_dirty")
            fence()
            after = replacement(before)
            target = safe_path(self.root, TARGET)
            fd, temporary = tempfile.mkstemp(prefix=".self-improvement-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(after)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, target.stat().st_mode)
                fence()
                if (
                    read_input(self.root, TARGET) != before
                    or read_input(self.root, RUNBOOK) != runbook
                ):
                    return self.transition(candidate, "stopped", "evidence_changed")
                os.replace(temporary, safe_path(self.root, TARGET))
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            fence()
            if read_input(self.root, TARGET) != after or read_input(self.root, RUNBOOK) != runbook:
                return self.transition(candidate, "failed", "postcondition_failed", "fail")
            return self.transition(candidate, "completed", "exact_local_change_verified", "pass")
        except PermissionError:
            return self.transition(candidate, "approval_wait", "permission_required", "unknown")
        except (TimeoutError, subprocess.TimeoutExpired):
            return self.transition(candidate, "unknown", "time_limit_result_unknown", "unknown")
        except (OSError, ValueError, subprocess.SubprocessError):
            return self.transition(candidate, "unknown", "local_operation_error", "unknown")

    def reconcile(self, candidate: str):
        row = self.candidate(candidate)
        if row["state"] != "unknown":
            return row
        packet = json.loads(row["packet"])
        if (
            packet["recipe"] == RECIPE
            and candidate == digest(json.dumps(packet, sort_keys=True).encode())
            and digest(read_input(self.root, TARGET)) == packet["expected_after"]
            and digest(read_input(self.root, RUNBOOK)) == packet["evidence"][RUNBOOK]
        ):
            return self.transition(
                candidate, "completed", "recovered_postcondition_verified", "pass"
            )
        return row

    def status(self):
        return {
            "config": self.config(),
            "candidates": [dict(row) for row in self.db.execute("SELECT * FROM candidates")],
            "events": [dict(row) for row in self.db.execute("SELECT * FROM events ORDER BY seq")],
            "observation": [
                json.loads(row[0]) for row in self.db.execute("SELECT data FROM observation")
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--max-attempts", type=int, default=1)
    init.add_argument("--max-seconds", type=float, default=120)
    init.add_argument("--max-api-calls", type=int, default=0)
    init.add_argument("--max-cost-microusd", type=int, default=0)
    sub.add_parser("scan")
    sub.add_parser("status")
    for command in ("run", "hold", "recover", "reconcile"):
        item = sub.add_parser(command)
        item.add_argument("candidate")
        if command == "run":
            item.add_argument("--owner", required=True, type=thread_id)
    observation = sub.add_parser("observe")
    observation.add_argument("--owner", required=True, type=thread_id)
    observation.add_argument("--event-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        with dispatcher(ROOT):
            runner = Runner(ROOT)
            try:
                if args.command == "init":
                    runner.init(
                        args.max_attempts,
                        args.max_seconds,
                        args.max_api_calls,
                        args.max_cost_microusd,
                    )
                    result = runner.status()
                elif args.command == "observe":
                    runner.config()
                    result = observe(args.event_file, args.owner)
                    with runner.db:
                        runner.db.execute(
                            "INSERT OR REPLACE INTO observation VALUES (1,?)", (json.dumps(result),)
                        )
                elif args.command == "run":
                    result = runner.run(args.candidate, args.owner)
                elif args.command in ("hold", "recover", "reconcile"):
                    result = getattr(runner, args.command)(args.candidate)
                else:
                    result = getattr(runner, args.command)()
                print(json.dumps(result, ensure_ascii=True, indent=2))
                state = result.get("state")
                return 0 if state in (None, "completed", "ready", "no_candidate") else 2
            finally:
                runner.close()
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        # 例外本文には外部入力やlocal pathが入り得るため、そのまま保存・表示しない。
        print(json.dumps({"state": "error", "error_type": type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
