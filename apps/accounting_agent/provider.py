import json
import subprocess
import tempfile
from pathlib import Path
from threading import Lock
from typing import Protocol

from .models import AgentDecision

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["tool", "publish", "stop"]},
        "next_tool": {"type": ["string", "null"]},
        "args_json": {"type": "string"},
        "proposal_json": {"type": ["string", "null"]},
        "short_public_reason": {"type": "string"},
    },
    "required": ["kind", "next_tool", "args_json", "proposal_json", "short_public_reason"],
}

TOOL_CATALOG = {
    "list_documents": "資料目録。args: {}",
    "search_documents": "本文検索。args: {query}",
    "read_document": "版とspan付き原文。args: {document_id}",
    "read_open_receivables": "請求残高と入金の正本。args: {}",
    "lookup_counterparty": "確認済み別名と候補。args: {text}",
    "find_allocation_candidates": "全候補と差額を計算。args: {receipt_id, invoice_ids}",
    "read_adjustment_history": "調整のdraft/approved/cancelled/applied。args: {invoice_id}",
    "ask_operator": "資料から決められない方針だけ質問。args: {question, reason, options}",
}


class DecisionProvider(Protocol):
    provider_name: str
    model_name: str

    def decide(self, context: dict) -> tuple[AgentDecision, dict]: ...


class GlobalCallBudget:
    def __init__(self, limit: int = 24, used: int = 0) -> None:
        if used < 0 or used > limit:
            raise ValueError("invalid_global_call_budget")
        self.limit = limit
        self.used = used
        self._lock = Lock()

    def take(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise RuntimeError("task_model_call_budget_exhausted")
            self.used += 1

    @property
    def remaining(self) -> int:
        return self.limit - self.used


class CodexExecProvider:
    provider_name = "Codex CLI / ChatGPT login"
    model_name = "CLI default (public ID unreported)"

    def __init__(self, budget: GlobalCallBudget, timeout_seconds: int = 60) -> None:
        self._budget = budget
        self._timeout = timeout_seconds

    @property
    def global_remaining(self) -> int:
        return self._budget.remaining

    def decide(self, context: dict) -> tuple[AgentDecision, dict]:
        self._budget.take()
        with tempfile.TemporaryDirectory(prefix="accounting-agent-") as directory:
            root = Path(directory)
            schema_path = root / "schema.json"
            output_path = root / "decision.json"
            schema_path.write_text(json.dumps(DECISION_SCHEMA), encoding="utf-8")
            completed = subprocess.run(
                self.command(root, schema_path, output_path),
                input=self._prompt(context),
                encoding="utf-8",
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
            if completed.returncode != 0 or not output_path.exists():
                detail = (
                    completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else ""
                )
                raise RuntimeError(f"model_call_failed:{completed.returncode}:{detail[:240]}")
            decision = AgentDecision.model_validate_json(output_path.read_text(encoding="utf-8"))
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
            'approval_policy="never"',
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
            "あなたは入金消込の調査判断だけを行う単一Agentです。file、shell、network、台帳更新は"
            "できません。最初はmissionと資料目録だけです。観測済みtool結果から次のallowlist toolを"
            "1つ選び、十分な根拠と全候補coverageを得たらproposalをpublishしてください。資料から決め"
            "られない業務方針だけask_operatorを使い、不明なら保留します。取消済み調整を有効扱いせず、"
            "金額を自分で補完しません。hidden reasoningは返さず短い公開理由だけを返します。"
            "publishの"
            "proposal_jsonはReconciliationProposal JSONで、evidenceには観測で発行されたsource refの"
            "完全なobjectだけを使います。\n\n"
            f"TOOLS:\n{json.dumps(TOOL_CATALOG, ensure_ascii=False)}\n\n"
            f"PUBLIC CONTEXT:\n{json.dumps(context, ensure_ascii=False)}"
        )


class FixedWorkflowProvider:
    """同じtool/solverを固定順で使う比較対象。hidden labelは参照しない。"""

    provider_name = "fixed workflow"
    model_name = "same extraction path / deterministic fixture parser"
    global_remaining = None

    def decide(self, context: dict) -> tuple[AgentDecision, dict]:
        receivables = self._tool_payload(context["trace"], "read_open_receivables")
        sequence = [
            ("list_documents", {}),
            ("read_open_receivables", {}),
        ]
        sequence.extend(
            ("read_document", {"document_id": item["document_id"]})
            for item in context["document_manifest"]
        )
        if receivables:
            sequence.extend(
                ("lookup_counterparty", {"text": item["payer_text"]})
                for item in receivables["receipts"]
            )
            sequence.extend(
                ("read_adjustment_history", {"invoice_id": item["invoice_id"]})
                for item in receivables["invoices"]
            )
            invoice_ids = sorted(item["invoice_id"] for item in receivables["invoices"])
            sequence.extend(
                (
                    "find_allocation_candidates",
                    {"receipt_id": item["receipt_id"], "invoice_ids": invoice_ids},
                )
                for item in receivables["receipts"]
            )
        for name, args in sequence:
            if not self._used_call(context["trace"], name, args):
                return AgentDecision(
                    kind="tool",
                    next_tool=name,
                    args_json=json.dumps(args),
                    proposal_json=None,
                    short_public_reason=f"固定workflow: {name}",
                ), {}
        facts = [
            ref
            for item in context["trace"]
            if item["name"] == "read_document"
            for ref in item["payload"].get("issued_refs", [])
        ]
        candidate = self._tool_payload(context["trace"], "find_allocation_candidates")
        receipt = receivables["receipts"][0]
        options = candidate["allocation_options"]
        unique = options[0] if len(options) == 1 else None
        allocations = []
        if unique:
            invoice_id = unique["invoice_ids"][0]
            evidence = [
                ref
                for ref in facts
                if (ref["fact_type"], ref["subject_id"])
                in {
                    ("receipt_amount", receipt["receipt_id"]),
                    ("invoice_open_amount", invoice_id),
                }
            ]
            allocations.append(
                {
                    "receipt_id": receipt["receipt_id"],
                    "invoice_id": invoice_id,
                    "cash_amount": receipt["amount"],
                    "adjustment_candidate": None,
                    "evidence": evidence,
                }
            )
        world = context["world"]
        proposal = {
            "proposal_id": f"proposal-{context['run_id']}",
            "version": 1,
            "world_id": world["world_id"],
            "based_on_revision": world["revision"],
            "tenant_id": world["tenant_id"],
            "receipt_id": receipt["receipt_id"],
            "allocations": allocations,
            "unapplied": ({"currency": "JPY", "minor_units": 0} if unique else receipt["amount"]),
            "coverage": {
                "eligible_invoice_ids": candidate["eligible_invoice_ids"],
                "considered_invoice_ids": candidate["considered_invoice_ids"],
                "status": candidate["coverage"],
            },
            "questions": (["複数の候補があるため対応する請求を営業へ確認"] if not unique else []),
        }
        return AgentDecision(
            kind="publish",
            next_tool=None,
            args_json="{}",
            proposal_json=json.dumps(proposal, ensure_ascii=False),
            short_public_reason="根拠、全候補、取消履歴を確認しレビューpackageを作成",
        ), {}

    @staticmethod
    def _used_call(trace: list[dict], name: str, args: dict) -> bool:
        return any(item["name"] == name and item.get("args") == args for item in trace)

    @staticmethod
    def _tool_payload(trace: list[dict], name: str) -> dict | None:
        return next((item["payload"] for item in trace if item["name"] == name), None)
