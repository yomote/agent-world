import json
from collections.abc import Iterator

import pytest
from accounting_agent.controller import AccountingAgentController
from accounting_agent.models import AgentDecision
from accounting_agent.provider import CodexExecProvider
from accounting_agent.store import RunStore
from world.accounting_simulator import AccountingSimulator


class SequenceProvider:
    provider_name = "test"
    model_name = "test"

    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions: Iterator[AgentDecision] = iter(decisions)

    def decide(self, context: dict) -> tuple[AgentDecision, dict]:
        return next(self.decisions), {}


class FailingProvider:
    provider_name = "test"
    model_name = "test"

    def decide(self, context: dict):
        raise TimeoutError("bounded timeout")


def _tool(name: str, args: dict) -> AgentDecision:
    return AgentDecision(
        kind="tool",
        next_tool=name,
        args_json=json.dumps(args),
        proposal_json=None,
        short_public_reason=f"{name}を確認",
    )


def test_observation_drives_tool_sequence_and_package_publication(tmp_path) -> None:
    # 回帰: 固定fixture成功表示ではなく、tool観測と発行済み根拠をpackageへ受け渡す。
    simulator = AccountingSimulator()
    store = RunStore(tmp_path / "runs.sqlite3")
    provider = SequenceProvider(
        [
            _tool("read_open_receivables", {}),
            _tool("read_document", {"document_id": "DOC-MAIL"}),
            _tool("read_document", {"document_id": "DOC-INVOICE"}),
        ]
    )
    controller = AccountingAgentController(simulator, store, provider)
    run_id = controller.start("agent")
    controller.advance(run_id, "action-1", 0)
    controller.advance(run_id, "action-2", 1)
    controller.advance(run_id, "action-3", 2)
    trace = [item for item in controller.view(run_id)["trace"] if item["kind"] == "tool"]
    mail = trace[-2]["payload"]["result"]
    invoice = trace[-1]["payload"]["result"]
    evidence = [
        next(ref for ref in mail["issued_refs"] if ref["span"] == "receipt"),
        next(ref for ref in invoice["issued_refs"] if ref["span"] == "balance"),
    ]
    proposal = {
        "proposal_id": "proposal-1",
        "version": 1,
        "world_id": "acct-known-01",
        "based_on_revision": 7,
        "tenant_id": "tenant-demo",
        "receipt_id": "RCPT-50000",
        "allocations": [
            {
                "receipt_id": "RCPT-50000",
                "invoice_id": "INV-70000",
                "cash_amount": {"currency": "JPY", "minor_units": 50000},
                "adjustment_candidate": None,
                "evidence": evidence,
            }
        ],
        "unapplied": {"currency": "JPY", "minor_units": 0},
        "coverage": {
            "eligible_invoice_ids": ["INV-70000"],
            "considered_invoice_ids": ["INV-70000"],
            "status": "complete",
        },
        "questions": ["残額20,000円を営業確認"],
    }
    provider.decisions = iter(
        [
            AgentDecision(
                kind="publish",
                next_tool=None,
                args_json="{}",
                proposal_json=json.dumps(proposal),
                short_public_reason="根拠を確認済み",
            )
        ]
    )
    view = controller.advance(run_id, "action-4", 3)
    assert view["status"] == "ready_for_review"
    assert view["artifact"]["payload"]["actual_ledger_updated"] is False
    assert simulator.observe().invoices[0].open_amount.minor_units == 70000


def test_provider_failure_counts_attempt_before_call_and_does_not_publish(tmp_path) -> None:
    # 回帰: timeout/schema failureを0 call扱いにせず、成功artifactへ化けさせない。
    store = RunStore(tmp_path / "runs.sqlite3")
    controller = AccountingAgentController(AccountingSimulator(), store, FailingProvider())
    run_id = controller.start("agent")
    with pytest.raises(TimeoutError):
        controller.advance(run_id, "action-1", 0)
    view = controller.view(run_id)
    assert view["model_attempts"] == 1
    assert view["model_successes"] == 0
    assert view["model_failures"] == 1
    assert view["artifact"] is None
    assert view["status"] == "unknown_terminal"


def test_restart_replays_completed_action_without_second_model_call(tmp_path) -> None:
    # 回帰: 応答喪失後に同じadvanceを再送してもdecision callを二重実行しない。
    path = tmp_path / "runs.sqlite3"
    first_provider = SequenceProvider([_tool("list_documents", {})])
    first = AccountingAgentController(AccountingSimulator(), RunStore(path), first_provider)
    run_id = first.start("agent")
    initial = first.advance(run_id, "action-1", 0)
    second_provider = FailingProvider()
    restarted = AccountingAgentController(AccountingSimulator(), RunStore(path), second_provider)
    replay = restarted.advance(run_id, "action-1", 0)
    assert replay["step_version"] == initial["step_version"] == 1
    assert replay["model_attempts"] == 1


def test_answer_receipt_replays_after_question_is_no_longer_pending(tmp_path) -> None:
    # 回帰: 成功した回答の応答喪失後、pending消失を理由に同じActionを失敗扱いしない。
    provider = SequenceProvider(
        [
            _tool(
                "ask_operator",
                {
                    "question": "どちらを確認しますか",
                    "reason": "資料だけでは決められない",
                    "options": ["sales", "hold"],
                },
            )
        ]
    )
    controller = AccountingAgentController(
        AccountingSimulator(), RunStore(tmp_path / "runs.sqlite3"), provider
    )
    run_id = controller.start("agent")
    waiting = controller.advance(run_id, "advance-1", 0)
    question = waiting["pending_question"]
    answered = controller.answer(run_id, "answer-1", 1, question["question_id"], "sales")
    replay = controller.answer(run_id, "answer-1", 1, question["question_id"], "sales")
    assert replay == answered
    assert replay["step_version"] == 2


def test_codex_command_isolated_from_repository_and_disables_builtin_tools(tmp_path) -> None:
    # 回帰: hidden fixture/評価labelをCodexのfile/shell能力から直読させない。
    command = CodexExecProvider.command(tmp_path, tmp_path / "schema.json", tmp_path / "out.json")
    joined = " ".join(str(value) for value in command)
    assert "shell_tool" in joined
    assert "browser_use" in joined
    assert "computer_use" in joined
    assert "multi_agent" in joined
    assert 'approval_policy="never"' in command
    assert "shell_environment_policy.inherit=none" in joined
    assert str(tmp_path) in command
    assert "agent-world" not in str(tmp_path)
