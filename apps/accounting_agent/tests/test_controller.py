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
        ]
    )
    controller = AccountingAgentController(simulator, store, provider)
    run_id = controller.start("agent")
    controller.advance(run_id)
    controller.advance(run_id)
    observed = controller._traces[run_id][-1]["payload"]
    evidence = next(ref for ref in observed["issued_refs"] if ref["span"] == "receipt")
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
                "evidence": [evidence],
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
    view = controller.advance(run_id)
    assert view["status"] == "ready_for_review"
    assert view["artifact"]["payload"]["actual_ledger_updated"] is False
    assert simulator.observe().invoices[0].open_amount.minor_units == 70000


def test_provider_failure_counts_attempt_before_call_and_does_not_publish(tmp_path) -> None:
    # 回帰: timeout/schema failureを0 call扱いにせず、成功artifactへ化けさせない。
    store = RunStore(tmp_path / "runs.sqlite3")
    controller = AccountingAgentController(AccountingSimulator(), store, FailingProvider())
    run_id = controller.start("agent")
    with pytest.raises(TimeoutError):
        controller.advance(run_id)
    view = controller.view(run_id)
    assert view["model_attempts"] == 1
    assert view["model_successes"] == 0
    assert view["model_failures"] == 1
    assert view["artifact"] is None


def test_codex_command_isolated_from_repository_and_disables_builtin_tools(tmp_path) -> None:
    # 回帰: hidden fixture/評価labelをCodexのfile/shell能力から直読させない。
    command = CodexExecProvider.command(tmp_path, tmp_path / "schema.json", tmp_path / "out.json")
    joined = " ".join(str(value) for value in command)
    assert "shell_tool" in joined
    assert "browser_use" in joined
    assert "computer_use" in joined
    assert "multi_agent" in joined
    assert "shell_environment_policy.inherit=none" in joined
    assert str(tmp_path) in command
    assert "agent-world" not in str(tmp_path)
