"""モデル判断、typed tool実行、予算、HITLをつなぐhost。"""

import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib import error, request
from uuid import uuid4

from pydantic import ValidationError
from world.incident_models import RecoveryProposal

from .models import AgentRun, ModelDecision, TraceEntry
from .provider import DecisionProvider

READ_TOOLS = {
    "observe_system",
    "query_events",
    "inspect_queue",
    "read_service_config",
    "list_changes",
    "search_knowledge",
    "probe_dependency",
    "advance_and_verify",
    "lookup_action_status",
}


class WorldGateway(Protocol):
    def post(self, path: str, payload: dict) -> dict: ...
    def get(self, path: str) -> dict: ...


class HttpWorldGateway:
    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        self.base_url = base_url.rstrip("/")
        self._capabilities = {
            "observer": os.environ["LAB_OBSERVER_CAPABILITY"],
            "proposer": os.environ["LAB_PROPOSER_CAPABILITY"],
            "operator": os.environ["LAB_OPERATOR_CAPABILITY"],
            "executor": os.environ["LAB_EXECUTOR_CAPABILITY"],
        }

    def get(self, path: str) -> dict:
        return self._send("GET", path, None, "observer")

    def post(self, path: str, payload: dict) -> dict:
        capability = "observer"
        if path == "/api/incidents/runs" or path.endswith("/approval"):
            capability = "operator"
        elif path.endswith("/apply"):
            capability = "executor"
        elif "/proposals" in path:
            capability = "proposer"
        elif path.endswith("/tools") and payload.get("tool") == "advance_and_verify":
            capability = "executor"
        return self._send("POST", path, payload, capability)

    def _send(self, method: str, path: str, payload: dict | None, capability: str) -> dict:
        body = None if payload is None else json.dumps(payload).encode()
        req = request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        req.add_header("X-Lab-Capability", self._capabilities[capability])
        try:
            with request.urlopen(req, timeout=10) as response:
                return json.loads(response.read())
        except (error.URLError, TimeoutError) as exc:
            raise RuntimeError("world_transport_unknown") from exc


@dataclass
class MutableRun:
    run_id: str
    scenario_number: int
    mode: str
    provider: DecisionProvider
    status: str
    world: dict
    trace: list[TraceEntry]
    model_calls: int = 0
    model_successes: int = 0
    model_failures: int = 0
    tool_calls: int = 0
    clarification_count: int = 0
    proposal_count: int = 0
    latest_reason: str = "観測を開始できます"
    pending_question: dict | None = None
    pending_proposal: dict | None = None
    proposal_hash: str | None = None
    execution_action_id: str | None = None
    verification_action_id: str | None = None
    reconciliation_action_id: str | None = None
    error: str | None = None


class IncidentController:
    MAX_MODEL_CALLS = 8
    MAX_TOOL_CALLS = 18
    MAX_CLARIFICATIONS = 2
    MAX_PROPOSALS = 2

    def __init__(self, world: WorldGateway, provider_factory) -> None:
        self._world = world
        self._provider_factory = provider_factory
        self._runs: dict[str, MutableRun] = {}

    def start(self, scenario_number: int, mode: str = "actual") -> AgentRun:
        world = self._world.post("/api/incidents/runs", {"scenario_number": scenario_number})
        run_id = world["summary"]["run_id"]
        run = MutableRun(
            run_id=run_id,
            scenario_number=scenario_number,
            mode=mode,
            provider=self._provider_factory(mode),
            status="observing",
            world=world,
            trace=[],
        )
        self._runs[run_id] = run
        return self.public(run_id)

    def public(self, run_id: str) -> AgentRun:
        run = self._require(run_id)
        return AgentRun(
            run_id=run.run_id,
            scenario_number=run.scenario_number,
            status=run.status,
            mode=run.mode,
            provider=run.provider.provider_name,
            model=run.provider.model_name,
            model_calls=run.model_calls,
            model_successes=run.model_successes,
            model_failures=run.model_failures,
            global_model_calls_remaining=getattr(run.provider, "global_remaining", None),
            tool_calls=run.tool_calls,
            clarification_count=run.clarification_count,
            proposal_count=run.proposal_count,
            latest_reason=run.latest_reason,
            trace=run.trace,
            world=run.world,
            pending_question=run.pending_question,
            pending_proposal=run.pending_proposal,
            proposal_hash=run.proposal_hash,
            execution_action_id=run.execution_action_id,
            verification_action_id=run.verification_action_id,
            reconciliation_action_id=run.reconciliation_action_id,
            error=run.error,
            artifacts=self._artifacts(run),
        )

    def advance(self, run_id: str) -> AgentRun:
        run = self._require(run_id)
        while run.status not in {
            "waiting_human",
            "waiting_approval",
            "recovered",
            "stopped",
            "unknown",
        }:
            if (
                run.mode == "actual" and run.model_calls >= self.MAX_MODEL_CALLS
            ) or run.tool_calls >= self.MAX_TOOL_CALLS:
                run.status = "stopped"
                run.error = "run_budget_exhausted"
                break
            try:
                if run.mode == "actual":
                    run.model_calls += 1
                decision, usage = run.provider.decide(self._context(run))
                if run.mode == "actual":
                    run.model_successes += 1
                run.latest_reason = decision.short_public_reason
                self._trace(
                    run,
                    "model",
                    decision.kind,
                    decision.short_public_reason,
                    decision.evidence_refs,
                    {"next_tool": decision.next_tool, "usage": usage},
                )
                self._handle_decision(run, decision)
            except (RuntimeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
                if run.mode == "actual":
                    run.model_failures += 1
                run.status = "unknown"
                run.error = str(exc)
                self._trace(
                    run,
                    "failure",
                    "runtime",
                    "結果不明のため自動再送せず停止",
                    [],
                    {"error": str(exc)},
                )
        return self.public(run_id)

    def answer(self, run_id: str, answer: str) -> AgentRun:
        run = self._require(run_id)
        if run.status != "waiting_human" or run.pending_question is None:
            raise ValueError("not_waiting_human")
        self._trace(
            run,
            "approval",
            "operator_answer",
            "業務上の選択を受領",
            [],
            {"answer": answer, "question": run.pending_question},
        )
        run.pending_question = None
        run.status = "investigating"
        return self.advance(run_id)

    def approve(self, run_id: str, approve: bool, _approver: str) -> AgentRun:
        run = self._require(run_id)
        if run.status != "waiting_approval" or run.proposal_hash is None:
            raise ValueError("not_waiting_approval")
        approval_action_id = str(uuid4())
        run.reconciliation_action_id = approval_action_id
        try:
            approval = self._world.post(
                f"/api/incidents/runs/{run_id}/approval",
                {
                    "action_id": approval_action_id,
                    "proposal_hash": run.proposal_hash,
                    "expected_revision": run.world["summary"]["revision"],
                    "approve": approve,
                },
            )
        except RuntimeError as exc:
            return self._unknown_write(run, exc)
        self._trace(
            run,
            "approval",
            approval["status"],
            "実行承認を記録",
            approval.get("event_refs", []),
            {},
        )
        run.world = self._world.get(f"/api/incidents/runs/{run_id}")
        if approval["status"] != "approved":
            run.status = "stopped"
            return self.public(run_id)
        action_id = str(uuid4())
        run.execution_action_id = action_id
        run.reconciliation_action_id = action_id
        run.status = "applying"
        try:
            applied = self._world.post(
                f"/api/incidents/runs/{run_id}/apply",
                {
                    "action_id": action_id,
                    "proposal_hash": run.proposal_hash,
                    "expected_revision": run.world["summary"]["revision"],
                },
            )
        except RuntimeError as exc:
            return self._unknown_write(run, exc)
        if applied["status"] != "applied":
            run.status = "unknown" if applied["status"] not in {"failure", "stale"} else "stopped"
            run.error = applied["status"]
            return self.public(run_id)
        run.world = self._world.get(f"/api/incidents/runs/{run_id}")
        self._trace(
            run,
            "approval",
            "applied",
            "承認済み差分をSimulatorが適用",
            applied.get("event_refs", []),
            {"action_id": action_id},
        )
        run.status = "verifying"
        return self.advance(run_id)

    def _handle_decision(self, run: MutableRun, decision: ModelDecision) -> None:
        if decision.kind == "stop":
            run.status = "stopped"
            return
        if decision.kind == "propose":
            if run.proposal_count >= self.MAX_PROPOSALS or not decision.proposal_json:
                raise ValueError("proposal_budget_or_payload_invalid")
            proposal = RecoveryProposal.model_validate_json(decision.proposal_json)
            run.proposal_count += 1
            validation = self._world.post(
                f"/api/incidents/runs/{run.run_id}/proposals/validate",
                proposal.model_dump(mode="json"),
            )
            self._trace(
                run,
                "proposal",
                "validate_proposal",
                decision.short_public_reason,
                validation.get("event_refs", []),
                validation,
            )
            if validation["status"] != "valid":
                run.status = "investigating"
                return
            request_action_id = str(uuid4())
            run.reconciliation_action_id = request_action_id
            requested = self._world.post(
                f"/api/incidents/runs/{run.run_id}/proposals",
                {
                    "action_id": request_action_id,
                    "proposal": proposal.model_dump(mode="json"),
                },
            )
            run.pending_proposal = proposal.model_dump(mode="json")
            run.proposal_hash = requested["proposal_hash"]
            run.world = self._world.get(f"/api/incidents/runs/{run.run_id}")
            run.status = "waiting_approval"
            return
        if decision.kind != "tool" or not decision.next_tool:
            raise ValueError("invalid_model_decision")
        args = json.loads(decision.args_json or "{}")
        if not isinstance(args, dict):
            raise ValueError("tool_args_must_be_object")
        if decision.next_tool == "ask_operator":
            if run.clarification_count >= self.MAX_CLARIFICATIONS:
                raise ValueError("clarification_budget_exhausted")
            run.clarification_count += 1
            run.pending_question = args
            run.status = "waiting_human"
            self._trace(
                run,
                "tool",
                "ask_operator",
                decision.short_public_reason,
                decision.evidence_refs,
                args,
            )
            return
        if decision.next_tool not in READ_TOOLS:
            raise ValueError("tool_not_allowed")
        if decision.next_tool == "advance_and_verify":
            if run.verification_action_id is None:
                run.verification_action_id = str(uuid4())
            run.reconciliation_action_id = run.verification_action_id
            args = {
                **args,
                "proposal_hash": run.proposal_hash,
                "action_id": run.execution_action_id,
                "verification_action_id": run.verification_action_id,
                "ticks": 4,
            }
        result = self._world.post(
            f"/api/incidents/runs/{run.run_id}/tools", {"tool": decision.next_tool, "args": args}
        )
        run.tool_calls += 1
        self._trace(
            run,
            "verification" if decision.next_tool == "advance_and_verify" else "tool",
            decision.next_tool,
            decision.short_public_reason,
            [item["ref"] for item in result["evidence"]],
            {"args": args, "result": result},
        )
        run.world = self._world.get(f"/api/incidents/runs/{run.run_id}")
        run.status = (
            "recovered" if run.world["summary"]["state"] == "recovered" else "investigating"
        )

    def _context(self, run: MutableRun) -> dict:
        return {
            "mission": (
                "Shipping instructions have made no progress for ten minutes. Investigate, "
                "propose an evidenced recovery, request approval before changes, then verify "
                "no omissions or duplicates."
            ),
            "budgets": {
                "model_calls_remaining": self.MAX_MODEL_CALLS - run.model_calls,
                "tool_calls_remaining": self.MAX_TOOL_CALLS - run.tool_calls,
            },
            "world_summary": run.world["summary"],
            "services": run.world["services"],
            "operator_answers": [
                entry.payload for entry in run.trace if entry.name == "operator_answer"
            ],
            "applied": {"proposal_hash": run.proposal_hash, "action_id": run.execution_action_id}
            if run.execution_action_id
            else None,
            "evidence_trace": [entry.model_dump(mode="json") for entry in run.trace[-16:]],
        }

    @staticmethod
    def _trace(
        run: MutableRun, kind: str, name: str, reason: str, refs: list[str], payload: dict
    ) -> None:
        run.trace.append(
            TraceEntry(
                sequence=len(run.trace) + 1,
                kind=kind,
                name=name,
                public_reason=reason,
                evidence_refs=refs,
                payload=payload,
            )
        )

    def _require(self, run_id: str) -> MutableRun:
        if run_id not in self._runs:
            raise KeyError(run_id)
        return self._runs[run_id]

    def reconcile(self, run_id: str) -> AgentRun:
        run = self._require(run_id)
        if run.status != "unknown" or run.reconciliation_action_id is None:
            raise ValueError("no_unknown_write_to_reconcile")
        result = self._world.post(
            f"/api/incidents/runs/{run_id}/tools",
            {
                "tool": "lookup_action_status",
                "args": {"action_id": run.reconciliation_action_id},
            },
        )
        run.tool_calls += 1
        status = result["evidence"][0]["data"]["status"]
        run.world = self._world.get(f"/api/incidents/runs/{run_id}")
        self._trace(
            run,
            "tool",
            "lookup_action_status",
            "不明writeを権威的receiptで照合",
            [result["evidence"][0]["ref"]],
            {"status": status},
        )
        if status == "unknown":
            return self.public(run_id)
        run.error = None
        run.status = run.world["summary"]["state"]
        return self.public(run_id)

    def _unknown_write(self, run: MutableRun, error: RuntimeError) -> AgentRun:
        run.status = "unknown"
        run.error = str(error)
        self._trace(
            run,
            "failure",
            "write_unknown",
            "write結果不明。再送せずaction status照合待ち",
            [],
            {"action_id": run.reconciliation_action_id},
        )
        return self.public(run.run_id)

    @staticmethod
    def _artifacts(run: MutableRun) -> dict:
        refs = list(dict.fromkeys(ref for entry in run.trace for ref in entry.evidence_refs))
        summary = run.world["summary"]
        return {
            "incident_report": {
                "symptom": "Shipping instructions made no progress for ten minutes.",
                "conclusion": run.pending_proposal["diagnosis"]
                if run.pending_proposal
                else "未確定",
                "evidence_refs": refs,
                "public_reason": run.latest_reason,
            },
            "recovery_proposal": run.pending_proposal,
            "verification_report": {
                "status": "recovered" if run.status == "recovered" else "未完了",
                "accepted": summary["accepted"],
                "completed": summary["completed"],
                "incomplete": summary["incomplete"],
                "duplicates": summary["duplicate_count"],
                "scope": "承認済みaction後のbounded tick",
            },
            "handoff_record": {
                "mode": run.mode,
                "provider": run.provider.provider_name,
                "model": run.provider.model_name,
                "proposal_hash": run.proposal_hash,
                "execution_action_id": run.execution_action_id,
                "next_condition": "未完了または重複が発生したら停止して再調査",
            },
        }
