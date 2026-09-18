import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from world.accounting_models import (
    AllocationLine,
    CandidateCoverage,
    MoneyJPY,
    ReconciliationProposal,
)
from world.accounting_simulator import AccountingSimulator


@given(cash=st.integers(min_value=0, max_value=90000), duplicate=st.booleans())
@settings(max_examples=60, deadline=None, derandomize=True)
def test_generated_proposals_preserve_ledger_and_only_valid_totals_pass(
    cash: int, duplicate: bool
) -> None:
    # 回帰: Hypothesisのshrink/replayで金額・重複の最小反例を残高不変と同時に検査する。
    simulator = AccountingSimulator()
    before = deepcopy(simulator.observe())
    observation = simulator.read_document("tenant-demo", "DOC-MAIL", "pbt-run", "call-1")
    ref = simulator.issued_evidence_ref(observation.evidence_id, "receipt")
    line = AllocationLine(
        receipt_id="RCPT-50000",
        invoice_id="INV-70000",
        cash_amount=MoneyJPY(minor_units=cash),
        evidence=[ref],
    )
    lines = [line, line.model_copy(deep=True)] if duplicate else [line]
    proposal = ReconciliationProposal(
        proposal_id="pbt",
        version=1,
        world_id="acct-known-01",
        based_on_revision=7,
        tenant_id="tenant-demo",
        receipt_id="RCPT-50000",
        allocations=lines,
        unapplied=MoneyJPY(minor_units=max(0, 50000 - cash * len(lines))),
        coverage=CandidateCoverage(
            eligible_invoice_ids=["INV-70000"],
            considered_invoice_ids=["INV-70000"],
            status="complete",
        ),
    )
    report = simulator.validate(proposal, "pbt-run")
    assert simulator.observe() == before
    assert report.valid is (not duplicate and cash <= 50000)


class ReadOnlyAccountingMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.simulator = AccountingSimulator()
        self.initial = deepcopy(self.simulator.observe())
        self.counter = 0

    @rule(document_id=st.sampled_from(["DOC-BANK", "DOC-INVOICE", "DOC-MAIL", "DOC-ADJ"]))
    def read(self, document_id: str) -> None:
        self.counter += 1
        self.simulator.read_document("tenant-demo", document_id, "stateful", f"call-{self.counter}")

    @rule(query=st.sampled_from(["alpha", "50,000", "取消", "missing"]))
    def search(self, query: str) -> None:
        self.simulator.search_documents("tenant-demo", query)

    @rule()
    def solve(self) -> None:
        self.simulator.find_allocation_candidates("tenant-demo", "RCPT-50000", ["INV-70000"])

    @invariant()
    def ledger_is_immutable(self) -> None:
        assert self.simulator.observe() == self.initial


TestReadOnlyAccountingMachine = ReadOnlyAccountingMachine.TestCase


@given(
    status=st.sampled_from(["draft", "approved", "cancelled", "applied"]),
    tenant=st.sampled_from(["tenant-demo", "tenant-other"]),
    reverse=st.booleans(),
)
@settings(max_examples=32, deadline=None, derandomize=True)
def test_adjustment_scope_and_input_order_properties(
    status: str, tenant: str, reverse: bool
) -> None:
    # 回帰: 入力順で結果を変えず、無効調整・tenant越境を生成例でも拒否する。
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "accounting_known.json").read_text(
            encoding="utf-8"
        )
    )
    second = deepcopy(fixture["snapshot"]["invoices"][0])
    second["invoice_id"] = "INV-30000"
    second["amount"]["minor_units"] = 30000
    second["open_amount"]["minor_units"] = 30000
    fixture["snapshot"]["invoices"].append(second)
    fixture["snapshot"]["adjustments"][0]["status"] = status
    with TemporaryDirectory(prefix="accounting-property-") as directory:
        fixture_path = Path(directory) / "fixture.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
        simulator = AccountingSimulator(fixture_path)
    if tenant != "tenant-demo":
        before = simulator.observe()
        try:
            simulator.find_allocation_candidates(tenant, "RCPT-50000", ["INV-70000"])
        except ValueError:
            pass
        else:
            raise AssertionError("cross-tenant candidate search must fail")
        assert simulator.observe() == before
        return
    ordered = ["INV-70000", "INV-30000"]
    first = simulator.find_allocation_candidates(
        tenant, "RCPT-50000", list(reversed(ordered)) if reverse else ordered
    )
    second_result = simulator.find_allocation_candidates(tenant, "RCPT-50000", ordered)
    assert first == second_result
    observation = simulator.read_document(tenant, "DOC-MAIL", "property-run", "call-1")
    evidence = simulator.issued_evidence_ref(observation.evidence_id, "receipt")
    proposal = ReconciliationProposal(
        proposal_id="property-adjustment",
        version=1,
        world_id="acct-known-01",
        based_on_revision=7,
        tenant_id=tenant,
        receipt_id="RCPT-50000",
        allocations=[
            AllocationLine(
                receipt_id="RCPT-50000",
                invoice_id="INV-70000",
                cash_amount=MoneyJPY(minor_units=50000),
                adjustment_candidate=MoneyJPY(minor_units=20000),
                evidence=[evidence],
            )
        ],
        unapplied=MoneyJPY(minor_units=0),
        coverage=CandidateCoverage(
            eligible_invoice_ids=ordered,
            considered_invoice_ids=ordered,
            status="complete",
        ),
    )
    report = simulator.validate(proposal, "property-run")
    assert report.valid is (status == "approved")
