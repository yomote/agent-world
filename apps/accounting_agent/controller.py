import json
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from world.accounting_models import ReconciliationProposal
from world.accounting_simulator import AccountingDomainError, AccountingSimulator

from .models import AgentDecision, Question, ReviewPackage
from .provider import DecisionProvider
from .store import RunStore

ALLOWED_TOOLS = {
    "list_documents",
    "search_documents",
    "read_document",
    "read_open_receivables",
    "lookup_counterparty",
    "find_allocation_candidates",
    "read_adjustment_history",
    "ask_operator",
}


class AccountingAgentController:
    """Runs model decisions; only this host receives read-only domain capabilities."""

    def __init__(
        self, simulator: AccountingSimulator, store: RunStore, provider: DecisionProvider
    ) -> None:
        self._simulator = simulator
        self._store = store
        self._provider = provider

    def start(self, mode: str) -> str:
        run_id = f"run-{uuid4()}"
        self._store.create_run(run_id, mode, self._simulator.fixture_id)
        return run_id

    def advance(self, run_id: str, action_id: str, expected_step_version: int) -> dict[str, Any]:
        try:
            claim = self._store.claim_operation(
                run_id,
                action_id,
                "advance",
                expected_step_version,
                {"run_id": run_id},
            )
        except ValueError as error:
            raise AccountingDomainError(str(error)) from error
        if claim == "replay":
            return self._replay(action_id)
        if claim == "in_progress":
            raise AccountingDomainError("operation_in_progress")
        if claim == "unknown":
            raise AccountingDomainError("operation_result_unknown")
        try:
            current = self._require_run(run_id)
            if self._store.pending_question(run_id):
                return self._complete(action_id, run_id, self.view(run_id))
            self._enforce_limits(current)
            context = self._context(run_id)
        except Exception as error:
            code = self._complete_failure(action_id, run_id, error)
            raise AccountingDomainError(code) from error
        self._store.record_attempt(run_id)
        try:
            decision, usage = self._provider.decide(context)
        except Exception as error:
            self._store.record_model_result(run_id, False)
            self._store.append_trace(
                run_id,
                "model_failure",
                "decision",
                {"code": type(error).__name__, "detail": str(error)[:200]},
            )
            self._store.mark_operation_unknown(action_id, run_id)
            raise
        self._store.record_model_result(run_id, True)
        decision_id = f"decision-{uuid4()}"
        self._store.append_trace(
            run_id,
            "decision",
            decision.kind,
            {
                "short_public_reason": decision.short_public_reason,
                "next_tool": decision.next_tool,
                "usage": usage,
            },
            decision_id=decision_id,
        )
        try:
            if decision.kind == "tool":
                self._store.increment(run_id, "tool_calls")
                self._execute_tool(run_id, decision_id, decision)
            elif decision.kind == "publish":
                self._store.increment(run_id, "proposal_count")
                self._publish(run_id, decision_id, decision)
            else:
                self._set_status(run_id, "stopped")
        except Exception as error:
            self._set_status(run_id, "domain_failed")
            code = self._complete_failure(action_id, run_id, error)
            raise AccountingDomainError(code) from error
        return self._complete(action_id, run_id, self.view(run_id))

    def answer(
        self,
        run_id: str,
        action_id: str,
        expected_step_version: int,
        question_id: str,
        answer: str,
    ) -> dict[str, Any]:
        try:
            claim = self._store.claim_operation(
                run_id,
                action_id,
                "answer",
                expected_step_version,
                {"question_id": question_id, "answer": answer},
            )
        except ValueError as error:
            raise AccountingDomainError(str(error)) from error
        if claim == "replay":
            return self._replay(action_id)
        if claim == "in_progress":
            raise AccountingDomainError("operation_in_progress")
        if claim == "unknown":
            raise AccountingDomainError("operation_result_unknown")
        pending = self._store.pending_question(run_id)
        if pending is None or pending["question_id"] != question_id:
            self._store.complete_operation(
                action_id, run_id, {"error": "question_not_pending"}, ok=False
            )
            raise AccountingDomainError("question_not_pending")
        question = Question.model_validate(pending)
        if answer not in question.options:
            self._store.complete_operation(
                action_id, run_id, {"error": "answer_not_in_options"}, ok=False
            )
            raise AccountingDomainError("answer_not_in_options")
        self._store.answer_question(run_id, question_id, answer)
        self._store.append_trace(
            run_id,
            "operator_answer",
            "ask_operator",
            {"question_id": question_id, "answer": answer},
        )
        return self._complete(action_id, run_id, self.view(run_id))

    def view(self, run_id: str) -> dict[str, Any]:
        value = self._require_run(run_id)
        value["pending_question"] = self._store.pending_question(run_id)
        return value

    def _execute_tool(self, run_id: str, decision_id: str, decision: AgentDecision) -> None:
        name = decision.next_tool
        if name not in ALLOWED_TOOLS:
            raise AccountingDomainError("tool_not_allowed")
        try:
            args = json.loads(decision.args_json)
            self._validate_tool_args(name, args)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
            raise AccountingDomainError("invalid_tool_arguments") from error
        call_id = f"tool-{uuid4()}"
        tenant = "tenant-demo"
        if name == "list_documents":
            result = self._simulator.list_documents(tenant)
        elif name == "search_documents":
            result = self._simulator.search_documents(tenant, str(args["query"]))
        elif name == "read_document":
            observation = self._simulator.read_document(
                tenant, str(args["document_id"]), run_id, call_id
            )
            result = observation.payload
            result["evidence_id"] = observation.evidence_id
            result["issued_refs"] = [
                self._simulator.issued_evidence_ref(observation.evidence_id, span).model_dump(
                    mode="json"
                )
                for span in result["spans"]
            ]
        elif name == "read_open_receivables":
            result = self._simulator.read_open_receivables(tenant)
        elif name == "lookup_counterparty":
            result = self._simulator.lookup_counterparty(tenant, str(args["text"]))
        elif name == "find_allocation_candidates":
            result = self._simulator.find_allocation_candidates(
                tenant, str(args["receipt_id"]), [str(value) for value in args["invoice_ids"]]
            )
        elif name == "read_adjustment_history":
            result = self._simulator.read_adjustment_history(
                tenant, str(args["invoice_id"]), run_id, call_id
            )
        else:
            question = Question(
                question_id=f"question-{uuid4()}",
                question=str(args["question"]),
                reason=str(args["reason"]),
                options=[str(value) for value in args["options"]],
            )
            self._store.increment(run_id, "question_count")
            self._store.save_question(
                run_id, question.question_id, question.model_dump(mode="json")
            )
            result = question.model_dump(mode="json")
        self._store.append_trace(
            run_id,
            "tool",
            name,
            {"args": args, "result": result},
            decision_id=decision_id,
            tool_call_id=call_id,
        )

    def _publish(self, run_id: str, decision_id: str, decision: AgentDecision) -> None:
        if decision.proposal_json is None:
            raise AccountingDomainError("proposal_missing")
        try:
            proposal = ReconciliationProposal.model_validate_json(decision.proposal_json)
        except ValidationError as error:
            raise AccountingDomainError("proposal_schema_invalid") from error
        report = self._simulator.validate(proposal, run_id)
        if not report.valid:
            self._store.append_trace(
                run_id,
                "domain_failure",
                "validate_reconciliation",
                report.model_dump(mode="json"),
                decision_id=decision_id,
            )
            raise AccountingDomainError("proposal_domain_invalid")
        payload = {
            "proposal": proposal.model_dump(mode="json"),
            "validation": report.model_dump(mode="json"),
        }
        digest = sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        package = ReviewPackage(
            artifact_id=f"package-{digest[:16]}",
            version=1,
            status="ready_for_review",
            run_id=run_id,
            proposal=proposal,
            validation=report,
            unresolved=proposal.questions,
            sales_inquiry=proposal.questions,
            source_provenance=[
                ref.model_dump(mode="json")
                for allocation in proposal.allocations
                for ref in allocation.evidence
            ],
            observed_adjustments=[
                item
                for entry in self._require_run(run_id)["trace"]
                if entry["kind"] == "tool" and entry["name"] == "read_adjustment_history"
                for item in entry["payload"]["result"]["history"]
            ],
        )
        self._store.save_artifact(
            package.artifact_id,
            run_id,
            package.version,
            "review_package",
            package.model_dump(mode="json"),
            digest,
        )
        self._store.append_trace(
            run_id,
            "artifact",
            "publish_package",
            {"artifact_id": package.artifact_id, "digest": digest},
            decision_id=decision_id,
        )

    def _context(self, run_id: str) -> dict[str, Any]:
        snapshot = self._simulator.observe()
        persisted = self._require_run(run_id)
        trace: list[dict[str, Any]] = []
        for item in persisted["trace"]:
            if item["kind"] != "tool":
                continue
            payload = item["payload"]
            result = payload["result"]
            refs = result.get("issued_refs", []) if isinstance(result, dict) else []
            self._simulator.restore_evidence(run_id, refs)
            trace.append(
                {"kind": "tool", "name": item["name"], "args": payload["args"], "payload": result}
            )
        return {
            "mission": "資料を調査し、入金消込のレビューpackageを作る。台帳は更新しない。",
            "run_id": run_id,
            "world": {
                "world_id": snapshot.world_id,
                "revision": snapshot.revision,
                "tenant_id": snapshot.tenant_id,
                "as_of": snapshot.as_of.isoformat(),
            },
            "document_manifest": self._simulator.list_documents(snapshot.tenant_id),
            "proposal_schema": ReconciliationProposal.model_json_schema(),
            "trace": trace,
            "operator_answers": self._store.answered_questions(run_id),
        }

    def _require_run(self, run_id: str) -> dict[str, Any]:
        value = self._store.get_run(run_id)
        if value is None:
            raise AccountingDomainError("unknown_run")
        return value

    def _set_status(self, run_id: str, status: str) -> None:
        self._store.set_status(run_id, status)

    def _complete(self, action_id: str, run_id: str, value: dict[str, Any]) -> dict[str, Any]:
        completed = {**value, "step_version": value["step_version"] + 1}
        self._store.complete_operation(action_id, run_id, completed)
        return completed

    def _replay(self, action_id: str) -> dict[str, Any]:
        receipt = self._store.operation_result(action_id)
        if not receipt["ok"]:
            raise AccountingDomainError(receipt["value"]["error"])
        return receipt["value"]

    def _complete_failure(self, action_id: str, run_id: str, error: Exception) -> str:
        code = str(error) if isinstance(error, AccountingDomainError) else "domain_failed"
        self._store.complete_operation(action_id, run_id, {"error": code}, ok=False)
        return code

    @staticmethod
    def _validate_tool_args(name: str, args: Any) -> None:
        if not isinstance(args, dict):
            raise ValueError("arguments_must_be_object")
        required: dict[str, dict[str, type]] = {
            "list_documents": {},
            "read_open_receivables": {},
            "search_documents": {"query": str},
            "read_document": {"document_id": str},
            "lookup_counterparty": {"text": str},
            "find_allocation_candidates": {"receipt_id": str, "invoice_ids": list},
            "read_adjustment_history": {"invoice_id": str},
            "ask_operator": {"question": str, "reason": str, "options": list},
        }
        specification = required[name]
        if set(args) != set(specification):
            raise ValueError("argument_keys_mismatch")
        if any(not isinstance(args[key], expected) for key, expected in specification.items()):
            raise TypeError("argument_type_mismatch")
        if name == "find_allocation_candidates" and not all(
            isinstance(value, str) for value in args["invoice_ids"]
        ):
            raise TypeError("invoice_ids_must_be_strings")
        if name == "ask_operator" and (
            len(args["options"]) < 2 or not all(isinstance(value, str) for value in args["options"])
        ):
            raise ValueError("options_invalid")

    def _enforce_limits(self, run: dict[str, Any]) -> None:
        limits = {
            "model_attempts": 11,
            "tool_calls": 18,
            "question_count": 2,
            "proposal_count": 2,
        }
        exceeded = next((name for name, limit in limits.items() if run[name] >= limit), None)
        if exceeded:
            self._set_status(run["run_id"], "budget_exhausted")
            raise AccountingDomainError(f"run_budget_exhausted:{exceeded}")
