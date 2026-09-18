import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from world.accounting_models import (
    AllocationLine,
    CandidateCoverage,
    EvidenceRef,
    MoneyJPY,
    ReconciliationProposal,
)
from world.accounting_simulator import AccountingDomainError, AccountingSimulator


def _proposal(simulator: AccountingSimulator, run_id: str = "run-1") -> ReconciliationProposal:
    observation = simulator.read_document("tenant-demo", "DOC-MAIL", run_id, "tool-1")
    receipt_ref = simulator.issued_evidence_ref(observation.evidence_id, "receipt")
    invoice = simulator.read_document("tenant-demo", "DOC-INVOICE", run_id, "tool-2")
    invoice_ref = simulator.issued_evidence_ref(invoice.evidence_id, "balance")
    return ReconciliationProposal(
        proposal_id="proposal-1",
        version=1,
        world_id="acct-known-01",
        based_on_revision=7,
        tenant_id="tenant-demo",
        receipt_id="RCPT-50000",
        allocations=[
            AllocationLine(
                receipt_id="RCPT-50000",
                invoice_id="INV-70000",
                cash_amount=MoneyJPY(minor_units=50000),
                evidence=[receipt_ref, invoice_ref],
            )
        ],
        unapplied=MoneyJPY(minor_units=0),
        coverage=CandidateCoverage(
            eligible_invoice_ids=["INV-70000"],
            considered_invoice_ids=["INV-70000"],
            status="complete",
        ),
        questions=["残額20,000円の回収予定を営業へ確認"],
    )


def test_valid_package_keeps_authoritative_ledger_unchanged() -> None:
    # 回帰: review package生成が提案額を台帳へ書き込む事故を防ぐ。
    simulator = AccountingSimulator()
    before = simulator.observe()
    report = simulator.validate(_proposal(simulator), "run-1")
    after = simulator.observe()
    assert report.valid
    assert report.ledger_balance_unchanged
    assert before == after
    assert report.planned_balance.minor_units == 20000


def test_cancelled_adjustment_cannot_complete_the_invoice() -> None:
    # 回帰: 取消済み20,000円値引きを足して70,000円完済扱いにしない。
    simulator = AccountingSimulator()
    proposal = _proposal(simulator)
    proposal.allocations[0].adjustment_candidate = MoneyJPY(minor_units=20000)
    report = simulator.validate(proposal, "run-1")
    assert not report.valid
    assert "cancelled_adjustment" in {issue.code for issue in report.issues}


def test_evidence_is_bound_to_the_run_that_observed_it() -> None:
    # 回帰: modelが捏造したsource spanや別runの根拠を採用しない。
    simulator = AccountingSimulator()
    proposal = _proposal(simulator, "run-1")
    report = simulator.validate(proposal, "run-2")
    assert not report.valid
    assert "invalid_evidence" in {issue.code for issue in report.issues}


def test_cross_tenant_tools_are_denied_without_mutation() -> None:
    # 回帰: read-only toolでも別tenantの資料を漏らさない。
    simulator = AccountingSimulator()
    before = simulator.observe()
    with pytest.raises(AccountingDomainError, match="scope_violation"):
        simulator.list_documents("tenant-other")
    assert simulator.observe() == before


@pytest.mark.parametrize("cash", [0, 1, 49999, 50000, 50001, 69999, 70000, 70001])
def test_generated_amount_boundaries_never_change_ledger(cash: int) -> None:
    # 回帰: 境界値を縮小可能な表に固定し、拒否経路でも残高を変えない。
    simulator = AccountingSimulator()
    proposal = _proposal(simulator)
    before = deepcopy(simulator.observe())
    proposal.allocations[0].cash_amount = MoneyJPY(minor_units=cash)
    proposal.unapplied = MoneyJPY(minor_units=max(0, 50000 - cash))
    report = simulator.validate(proposal, "run-1")
    assert simulator.observe() == before
    assert report.valid is (cash <= 50000)


def test_duplicate_and_incomplete_candidate_coverage_are_rejected() -> None:
    # 回帰: 一部候補だけをmodelが恣意的に選ぶことと同一配分の二重計上を防ぐ。
    simulator = AccountingSimulator()
    proposal = _proposal(simulator)
    proposal.allocations.append(proposal.allocations[0].model_copy(deep=True))
    proposal.coverage.considered_invoice_ids = []
    proposal.coverage.status = "incomplete"
    report = simulator.validate(proposal, "run-1")
    assert {issue.code for issue in report.issues} >= {
        "duplicate_allocation",
        "incomplete_coverage",
        "over_allocation",
    }


def test_source_span_must_exist_in_issued_registry() -> None:
    # 回帰: 既存artifact IDに架空spanを付けた根拠を通さない。
    simulator = AccountingSimulator()
    proposal = _proposal(simulator)
    proposal.allocations[0].evidence = [
        EvidenceRef(
            artifact_id="DOC-MAIL",
            version=1,
            span="fabricated",
            observed_at=simulator.observe().as_of,
            fact_type="receipt_amount",
            subject_id="RCPT-50000",
            value="50000",
        )
    ]
    report = simulator.validate(proposal, "run-1")
    assert "invalid_evidence" in {issue.code for issue in report.issues}


def test_adjustment_history_issues_run_bound_evidence() -> None:
    # 回帰: authoritative履歴toolが返した取消根拠を捏造扱いせずrunへ束縛する。
    simulator = AccountingSimulator()
    result = simulator.read_adjustment_history(
        "tenant-demo", "INV-70000", "adjustment-run", "call-1"
    )
    assert result["history"][0]["status"] == "cancelled"
    assert result["issued_refs"][0]["artifact_id"] == "DOC-ADJ"


def test_unrelated_adjustment_fact_cannot_support_cash_allocation() -> None:
    # 回帰: 実在する無関係spanを金額根拠として流用するevidence launderingを防ぐ。
    simulator = AccountingSimulator()
    adjustment = simulator.read_adjustment_history(
        "tenant-demo", "INV-70000", "launder-run", "call-1"
    )
    proposal = _proposal(simulator, "launder-run")
    proposal.allocations[0].evidence = [
        EvidenceRef.model_validate(value) for value in adjustment["issued_refs"]
    ]
    report = simulator.validate(proposal, "launder-run")
    assert not report.valid
    assert "invalid_evidence" in {issue.code for issue in report.issues}


def test_mixed_tenant_records_do_not_affect_demo_scope_or_totals(tmp_path) -> None:
    # 回帰: 同じsnapshot fileに別tenant行があってもquery/coverage/残高へ混入させない。
    source = Path(__file__).parents[1] / "fixtures" / "accounting_known.json"
    fixture = json.loads(source.read_text(encoding="utf-8"))
    foreign_invoice = deepcopy(fixture["snapshot"]["invoices"][0])
    foreign_invoice.update(invoice_id="FOREIGN-I", tenant_id="tenant-other")
    foreign_receipt = deepcopy(fixture["snapshot"]["receipts"][0])
    foreign_receipt.update(receipt_id="FOREIGN-R", tenant_id="tenant-other")
    fixture["snapshot"]["invoices"].append(foreign_invoice)
    fixture["snapshot"]["receipts"].append(foreign_receipt)
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    simulator = AccountingSimulator(path)
    observed = simulator.read_open_receivables("tenant-demo")
    assert [item["invoice_id"] for item in observed["invoices"]] == ["INV-70000"]
    assert [item["receipt_id"] for item in observed["receipts"]] == ["RCPT-50000"]
    report = simulator.validate(_proposal(simulator, "mixed-run"), "mixed-run")
    assert report.planned_balance.minor_units == 20000


def test_money_rejects_non_jpy_fractional_or_negative_values() -> None:
    # 回帰: 通貨換算・小数円・負数をmodel出力から暗黙補正しない。
    for payload in [
        {"currency": "USD", "minor_units": 1},
        {"currency": "JPY", "minor_units": -1},
        {"currency": "JPY", "minor_units": 1.5},
    ]:
        with pytest.raises(ValidationError):
            MoneyJPY.model_validate(payload)
