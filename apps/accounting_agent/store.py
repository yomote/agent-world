import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4


class RunStore:
    """Local durable audit trail. Public summaries only; no hidden reasoning is stored."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._owner_token = str(uuid4())
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, status TEXT NOT NULL,
              fixture_id TEXT NOT NULL, created_at TEXT NOT NULL,
              model_attempts INTEGER NOT NULL DEFAULT 0,
              model_successes INTEGER NOT NULL DEFAULT 0,
              model_failures INTEGER NOT NULL DEFAULT 0,
              step_version INTEGER NOT NULL DEFAULT 0,
              tool_calls INTEGER NOT NULL DEFAULT 0,
              question_count INTEGER NOT NULL DEFAULT 0,
              proposal_count INTEGER NOT NULL DEFAULT 0,
              deadline_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trace (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL, decision_id TEXT, tool_call_id TEXT,
              kind TEXT NOT NULL, name TEXT NOT NULL, payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
              artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, version INTEGER NOT NULL,
              kind TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS questions (
              question_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
              payload_json TEXT NOT NULL, status TEXT NOT NULL,
              answer TEXT
            );
            CREATE TABLE IF NOT EXISTS operation_receipts (
              action_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
              operation TEXT NOT NULL, fingerprint TEXT NOT NULL,
              expected_step_version INTEGER NOT NULL,
              status TEXT NOT NULL, result_json TEXT,
              owner_token TEXT,
              created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_pending_operation_per_run
              ON operation_receipts(run_id) WHERE status='pending';
            """
        )
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(operation_receipts)").fetchall()
        }
        if "owner_token" not in columns:
            self._connection.execute("ALTER TABLE operation_receipts ADD COLUMN owner_token TEXT")
            self._connection.commit()

    def create_run(self, run_id: str, mode: str, fixture_id: str) -> None:
        self._connection.execute(
            """INSERT INTO runs(
                 run_id,mode,status,fixture_id,created_at,deadline_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                run_id,
                mode,
                "running",
                fixture_id,
                self._now(),
                (datetime.now(UTC) + timedelta(seconds=180)).isoformat(),
            ),
        )
        self._connection.commit()

    def record_attempt(self, run_id: str) -> None:
        self._connection.execute(
            "UPDATE runs SET model_attempts=model_attempts+1 WHERE run_id=?", (run_id,)
        )
        self._connection.commit()

    def record_model_result(self, run_id: str, success: bool) -> None:
        column = "model_successes" if success else "model_failures"
        self._connection.execute(f"UPDATE runs SET {column}={column}+1 WHERE run_id=?", (run_id,))
        self._connection.commit()

    def append_trace(
        self,
        run_id: str,
        kind: str,
        name: str,
        payload: dict[str, Any],
        *,
        decision_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        self._connection.execute(
            """INSERT INTO trace(run_id,decision_id,tool_call_id,kind,name,payload_json,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                run_id,
                decision_id,
                tool_call_id,
                kind,
                name,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                self._now(),
            ),
        )
        self._connection.commit()

    def set_status(self, run_id: str, status: str) -> None:
        self._connection.execute("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))
        self._connection.commit()

    def increment(self, run_id: str, column: str) -> None:
        if column not in {"tool_calls", "question_count", "proposal_count"}:
            raise ValueError("invalid_counter")
        self._connection.execute(f"UPDATE runs SET {column}={column}+1 WHERE run_id=?", (run_id,))
        self._connection.commit()

    def save_question(self, run_id: str, question_id: str, payload: dict) -> None:
        self._connection.execute(
            "INSERT INTO questions(question_id,run_id,payload_json,status) VALUES(?,?,?,'pending')",
            (question_id, run_id, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        self._connection.commit()

    def pending_question(self, run_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT payload_json FROM questions WHERE run_id=? AND status='pending'",
            (run_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def answer_question(self, run_id: str, question_id: str, answer: str) -> None:
        changed = self._connection.execute(
            """UPDATE questions SET status='answered',answer=?
               WHERE run_id=? AND question_id=? AND status='pending'""",
            (answer, run_id, question_id),
        ).rowcount
        if changed != 1:
            raise ValueError("question_not_pending")
        self._connection.commit()

    def answered_questions(self, run_id: str) -> list[dict[str, str]]:
        rows = self._connection.execute(
            "SELECT question_id,answer FROM questions WHERE run_id=? AND status='answered'",
            (run_id,),
        ).fetchall()
        return [{"question_id": row[0], "answer": row[1]} for row in rows]

    def claim_operation(
        self,
        run_id: str,
        action_id: str,
        operation: str,
        expected_step_version: int,
        payload: dict,
    ) -> str:
        canonical = json.dumps(
            {
                "operation": operation,
                "expected_step_version": expected_step_version,
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = __import__("hashlib").sha256(canonical.encode()).hexdigest()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """SELECT run_id,operation,fingerprint,status,owner_token
                       FROM operation_receipts WHERE action_id=?""",
                    (action_id,),
                ).fetchone()
                if existing:
                    if tuple(existing[:3]) != (run_id, operation, fingerprint):
                        raise ValueError("action_id_conflict")
                    if existing[3] == "completed":
                        outcome = "replay"
                    elif existing[3] == "pending" and existing[4] == self._owner_token:
                        outcome = "in_progress"
                    else:
                        self._connection.execute(
                            "UPDATE runs SET status='unknown_terminal' WHERE run_id=?", (run_id,)
                        )
                        outcome = "unknown"
                    self._connection.commit()
                    return outcome
                pending = self._connection.execute(
                    """SELECT owner_token FROM operation_receipts
                       WHERE run_id=? AND status='pending'""",
                    (run_id,),
                ).fetchone()
                if pending:
                    if pending[0] == self._owner_token:
                        self._connection.commit()
                        return "in_progress"
                    self._connection.execute(
                        "UPDATE runs SET status='unknown_terminal' WHERE run_id=?", (run_id,)
                    )
                    self._connection.commit()
                    return "unknown"
                run = self._connection.execute(
                    "SELECT status,step_version,deadline_at FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if run is None:
                    raise ValueError("unknown_run")
                if run[0] != "running":
                    raise ValueError("run_not_running")
                if run[1] != expected_step_version:
                    raise ValueError("stale_step_version")
                if datetime.fromisoformat(run[2]) <= datetime.now(UTC):
                    self._connection.execute(
                        "UPDATE runs SET status='budget_exhausted' WHERE run_id=?", (run_id,)
                    )
                    self._connection.commit()
                    raise ValueError("wall_budget_exhausted")
                self._connection.execute(
                    """INSERT INTO operation_receipts(
                         action_id,run_id,operation,fingerprint,expected_step_version,status,
                         owner_token,created_at
                       ) VALUES(?,?,?,?,?,'pending',?,?)""",
                    (
                        action_id,
                        run_id,
                        operation,
                        fingerprint,
                        expected_step_version,
                        self._owner_token,
                        self._now(),
                    ),
                )
                self._connection.commit()
                return "new"
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def complete_operation(
        self, action_id: str, run_id: str, value: dict | None = None, *, ok: bool = True
    ) -> None:
        result_json = json.dumps(
            {"ok": ok, "value": value or {}}, ensure_ascii=False, sort_keys=True
        )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                changed = self._connection.execute(
                    """UPDATE operation_receipts SET status='completed',result_json=?
                       WHERE action_id=? AND run_id=? AND status='pending'""",
                    (result_json, action_id, run_id),
                ).rowcount
                if changed != 1:
                    raise ValueError("operation_result_unknown")
                self._connection.execute(
                    "UPDATE runs SET step_version=step_version+1 WHERE run_id=?", (run_id,)
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def operation_result(self, action_id: str) -> dict:
        row = self._connection.execute(
            "SELECT status,result_json FROM operation_receipts WHERE action_id=?", (action_id,)
        ).fetchone()
        if row is None or row[0] != "completed" or row[1] is None:
            raise ValueError("operation_result_unknown")
        return json.loads(row[1])

    def mark_operation_unknown(self, action_id: str, run_id: str) -> None:
        self._connection.execute(
            "UPDATE operation_receipts SET status='unknown' WHERE action_id=? AND run_id=?",
            (action_id, run_id),
        )
        self._connection.execute(
            "UPDATE runs SET status='unknown_terminal' WHERE run_id=?", (run_id,)
        )
        self._connection.commit()

    def save_artifact(
        self, artifact_id: str, run_id: str, version: int, kind: str, payload: dict, digest: str
    ) -> None:
        existing = self._connection.execute(
            "SELECT run_id,version,kind,payload_json,digest FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if existing is not None:
            fingerprint = (run_id, version, kind, canonical, digest)
            if tuple(existing) == fingerprint:
                return
            raise ValueError("artifact_id_conflict")
        self._connection.execute(
            """INSERT INTO artifacts(artifact_id,run_id,version,kind,payload_json,digest,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (
                artifact_id,
                run_id,
                version,
                kind,
                canonical,
                digest,
                self._now(),
            ),
        )
        self._connection.execute(
            "UPDATE runs SET status='ready_for_review' WHERE run_id=?", (run_id,)
        )
        self._connection.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            return None
        trace = self._connection.execute(
            "SELECT * FROM trace WHERE run_id=? ORDER BY sequence", (run_id,)
        ).fetchall()
        artifact = self._connection.execute(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY version DESC LIMIT 1", (run_id,)
        ).fetchone()
        return {
            **dict(run),
            "trace": [
                {
                    **dict(item),
                    "payload": json.loads(item["payload_json"]),
                }
                for item in trace
            ],
            "artifact": ({**dict(artifact), "payload": json.loads(artifact["payload_json"])})
            if artifact
            else None,
        }

    def list_runs(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        return [dict(item) for item in rows]

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
