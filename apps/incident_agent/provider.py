"""Codex CLIを構造化された判断器としてだけ使うadapter。"""

import json
import subprocess
import tempfile
from pathlib import Path
from threading import Lock
from typing import Protocol

from .models import ModelDecision

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["tool", "propose", "stop"]},
        "next_tool": {"type": ["string", "null"]},
        "args_json": {"type": "string"},
        "proposal_json": {"type": ["string", "null"]},
        "short_public_reason": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "kind",
        "next_tool",
        "args_json",
        "proposal_json",
        "short_public_reason",
        "evidence_refs",
    ],
}

TOOLS = {
    "observe_system": "Current service health and aggregate counts. Args: {}.",
    "query_events": "Bounded service events. Args: service.",
    "inspect_queue": "Queue counts and samples. Args: partition (optional).",
    "read_service_config": "Current config. Args: service=worker|carrier.",
    "list_changes": "Recent changes. Args: service.",
    "search_knowledge": "Versioned operating knowledge. Args: query.",
    "probe_dependency": "Read-only carrier probe. Args: service=carrier.",
    "ask_operator": (
        "Ask only for a business trade-off unavailable in tools. Args: question, reason, options."
    ),
    "advance_and_verify": "After approved apply only. Args: ticks, proposal_hash, action_id.",
    "lookup_action_status": "Resolve an uncertain write without replay. Args: action_id.",
}


class DecisionProvider(Protocol):
    provider_name: str
    model_name: str

    def decide(self, context: dict) -> tuple[ModelDecision, dict]: ...


class GlobalCallBudget:
    def __init__(self, limit: int = 16) -> None:
        self.limit = limit
        self.used = 0
        self._lock = Lock()

    def take(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise RuntimeError("task_model_call_budget_exhausted")
            self.used += 1


class CodexExecProvider:
    provider_name = "Codex CLI / ChatGPT login"
    model_name = "CLI default (ID unreported)"

    def __init__(self, budget: GlobalCallBudget, timeout_seconds: int = 45) -> None:
        self._budget = budget
        self._timeout = timeout_seconds

    @property
    def global_remaining(self) -> int:
        return self._budget.limit - self._budget.used

    def decide(self, context: dict) -> tuple[ModelDecision, dict]:
        self._budget.take()
        with tempfile.TemporaryDirectory(prefix="incident-agent-") as directory:
            root = Path(directory)
            schema_path = root / "schema.json"
            output_path = root / "decision.json"
            schema_path.write_text(json.dumps(DECISION_SCHEMA), encoding="utf-8")
            prompt = self._prompt(context)
            command = self.command(root, schema_path, output_path)
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
            if completed.returncode != 0 or not output_path.exists():
                raise RuntimeError(f"model_call_failed:{completed.returncode}")
            decision = ModelDecision.model_validate_json(output_path.read_text(encoding="utf-8"))
            usage: dict = {}
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "turn.completed":
                    usage = event.get("usage", {})
            return decision, usage

    @staticmethod
    def command(root: Path, schema_path: Path, output_path: Path) -> list[str]:
        return [
            "codex",
            "-a",
            "never",
            "exec",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--disable",
            "shell_tool",
            "--disable",
            "browser_use",
            "--disable",
            "computer_use",
            "--disable",
            "apps",
            "--disable",
            "image_generation",
            "--disable",
            "multi_agent",
            "-c",
            "shell_environment_policy.inherit=none",
            "-C",
            str(root),
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            "-",
        ]

    @staticmethod
    def _prompt(context: dict) -> str:
        return (
            "You are the decision component of a bounded incident recovery agent. "
            "You cannot execute tools or read files. Choose one next tool from the supplied "
            "catalog, or propose a typed recovery after enough corroborating evidence, or stop "
            "when uncertain. "
            "Never reveal hidden reasoning; give a short public reason. Do not guess identifiers. "
            "A proposal JSON must contain expected_revision, diagnosis, evidence_refs, changes, "
            "rollback. "
            "Allowed changes are set_config(worker,subscription), set_config(carrier,route), "
            "select_deployment(worker,v1|v2), and requeue(queue, order_ids, "
            "expected_state=dead_letter). For a carrier spare-route trade-off, ask_operator before "
            "proposing unless an operator answer exists. After an approved application, call "
            "advance_and_verify with the provided proposal hash and action id.\n\n"
            f"TOOL CATALOG:\n{json.dumps(TOOLS, ensure_ascii=False)}\n\n"
            f"PUBLIC RUN CONTEXT:\n{json.dumps(context, ensure_ascii=False)}"
        )


class ScriptedProvider:
    """Unit tests only. UI labels this baseline and never as an actual Agent."""

    provider_name = "deterministic runbook"
    model_name = "none"
    global_remaining = None

    def __init__(self, decisions: list[ModelDecision]) -> None:
        self._decisions = iter(decisions)

    def decide(self, context: dict) -> tuple[ModelDecision, dict]:
        return next(self._decisions), {}


class RunbookProvider:
    """公開観測だけを使う同条件baseline。隠しscenario manifestは参照しない。"""

    provider_name = "deterministic runbook"
    model_name = "none"
    global_remaining = None

    def decide(self, context: dict) -> tuple[ModelDecision, dict]:
        trace = context["evidence_trace"]
        used = [entry["name"] for entry in trace if entry["kind"] in {"tool", "verification"}]
        sequence = [
            ("observe_system", {}),
            ("inspect_queue", {"limit": 20}),
            ("read_service_config", {"service": "worker"}),
            ("query_events", {"service": "worker"}),
            ("probe_dependency", {"service": "carrier"}),
            ("search_knowledge", {"query": "queue subscription parser schema carrier spare route"}),
            ("list_changes", {"service": "worker"}),
        ]
        for name, args in sequence:
            if name not in used:
                return ModelDecision(
                    kind="tool",
                    next_tool=name,
                    args_json=json.dumps(args),
                    proposal_json=None,
                    short_public_reason=f"runbook: {name}",
                    evidence_refs=[],
                ), {}
        applied = context.get("applied")
        if applied:
            return ModelDecision(
                kind="tool",
                next_tool="advance_and_verify",
                args_json=json.dumps({"ticks": 4, **applied}),
                proposal_json=None,
                short_public_reason="runbook: bounded recovery verification",
                evidence_refs=[],
            ), {}
        answers = context.get("operator_answers", [])
        queue = self._tool_payload(trace, "inspect_queue")
        config = self._tool_payload(trace, "read_service_config")
        probe = self._tool_payload(trace, "probe_dependency")
        evidence_refs = [ref for entry in trace for ref in entry.get("evidence_refs", [])]
        sample = self._first_evidence(queue).get("sample", [])
        counts = self._first_evidence(queue).get("counts", {})
        worker = self._first_evidence(config).get("config", {})
        carrier = self._first_evidence(probe)
        changes: list[dict] = []
        diagnosis = ""
        if counts.get("dead_letter", 0):
            diagnosis = (
                "Queue samples and parser configuration show an incompatible payload parser."
            )
            changes = [
                {
                    "operation": "select_deployment",
                    "service": "worker",
                    "key": None,
                    "value": "v2",
                    "order_ids": [],
                    "expected_state": None,
                },
                {
                    "operation": "requeue",
                    "service": "queue",
                    "key": None,
                    "value": None,
                    "order_ids": [item["id"] for item in sample],
                    "expected_state": "dead_letter",
                },
            ]
        elif sample and worker.get("subscription") != sample[0].get("partition"):
            diagnosis = "Queue partition and worker subscription differ."
            changes = [
                {
                    "operation": "set_config",
                    "service": "worker",
                    "key": "subscription",
                    "value": sample[0]["partition"],
                    "order_ids": [],
                    "expected_state": None,
                }
            ]
        elif carrier.get("status") == 429:
            if not answers:
                return ModelDecision(
                    kind="tool",
                    next_tool="ask_operator",
                    args_json=json.dumps(
                        {
                            "question": (
                                "Use the spare route for this run with up to 30 minutes tracking "
                                "delay, or wait for primary?"
                            ),
                            "reason": (
                                "Primary is rate limited and the trade-off is a business decision."
                            ),
                            "options": ["use_spare", "wait_primary"],
                        }
                    ),
                    proposal_json=None,
                    short_public_reason="Operator must choose the tracking-delay trade-off.",
                    evidence_refs=evidence_refs,
                ), {}
            if answers[-1].get("answer") == "wait_primary":
                return ModelDecision(
                    kind="stop",
                    next_tool=None,
                    args_json="{}",
                    proposal_json=None,
                    short_public_reason="Operator chose to wait for primary recovery.",
                    evidence_refs=evidence_refs,
                ), {}
            diagnosis = "Primary carrier route is rate limited; operator allowed the spare route."
            changes = [
                {
                    "operation": "set_config",
                    "service": "carrier",
                    "key": "route",
                    "value": "spare",
                    "order_ids": [],
                    "expected_state": None,
                }
            ]
        if not changes:
            return ModelDecision(
                kind="stop",
                next_tool=None,
                args_json="{}",
                proposal_json=None,
                short_public_reason="Runbook could not determine a safe change.",
                evidence_refs=evidence_refs,
            ), {}
        proposal = {
            "expected_revision": context["world_summary"]["revision"],
            "diagnosis": diagnosis,
            "evidence_refs": evidence_refs[:20] or ["runbook:no-ref"],
            "changes": changes,
            "rollback": "Restore the prior configuration and stop processing.",
        }
        return ModelDecision(
            kind="propose",
            next_tool=None,
            args_json="{}",
            proposal_json=json.dumps(proposal),
            short_public_reason=diagnosis,
            evidence_refs=evidence_refs,
        ), {}

    @staticmethod
    def _tool_payload(trace: list[dict], name: str) -> dict:
        return next(entry["payload"]["result"] for entry in trace if entry["name"] == name)

    @staticmethod
    def _first_evidence(result: dict) -> dict:
        return result["evidence"][0]["data"] if result.get("evidence") else {}
