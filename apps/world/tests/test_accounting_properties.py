import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from accounting_agent.store import RunStore
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
    receipt_ref = simulator.issued_evidence_ref(observation.evidence_id, "receipt")
    invoice = simulator.read_document("tenant-demo", "DOC-INVOICE", "pbt-run", "call-2")
    invoice_ref = simulator.issued_evidence_ref(invoice.evidence_id, "balance")
    line = AllocationLine(
        receipt_id="RCPT-50000",
        invoice_id="INV-70000",
        cash_amount=MoneyJPY(minor_units=cash),
        evidence=[receipt_ref, invoice_ref],
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
        self.directory = TemporaryDirectory(prefix="accounting-stateful-")
        fixture = json.loads(
            (Path(__file__).parents[1] / "fixtures" / "accounting_known.json").read_text(
                encoding="utf-8"
            )
        )
        fixture["snapshot"]["revision"] = 8
        next(item for item in fixture["documents"] if item["document_id"] == "DOC-MAIL")[
            "version"
        ] = 2
        updated_path = Path(self.directory.name) / "updated.json"
        updated_path.write_text(json.dumps(fixture), encoding="utf-8")
        self.updated_simulator = AccountingSimulator(updated_path)
        self.store = RunStore(Path(self.directory.name) / "runs.sqlite3")
        self.store.create_run("stateful-run", "baseline", "generated")
        self.validated_payload: dict | None = None
        self.published_payload: dict | None = None

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

    @rule(stale=st.booleans(), source_updated=st.booleans())
    def propose_validate_and_revalidate(self, stale: bool, source_updated: bool) -> None:
        self.counter += 1
        mail = self.simulator.read_document(
            "tenant-demo", "DOC-MAIL", "stateful-run", f"mail-{self.counter}"
        )
        invoice = self.simulator.read_document(
            "tenant-demo", "DOC-INVOICE", "stateful-run", f"invoice-{self.counter}"
        )
        proposal = ReconciliationProposal(
            proposal_id=f"stateful-{self.counter}",
            version=1,
            world_id="acct-known-01",
            based_on_revision=6 if stale else 7,
            tenant_id="tenant-demo",
            receipt_id="RCPT-50000",
            allocations=[
                AllocationLine(
                    receipt_id="RCPT-50000",
                    invoice_id="INV-70000",
                    cash_amount=MoneyJPY(minor_units=50000),
                    evidence=[
                        self.simulator.issued_evidence_ref(mail.evidence_id, "receipt"),
                        self.simulator.issued_evidence_ref(invoice.evidence_id, "balance"),
                    ],
                )
            ],
            unapplied=MoneyJPY(minor_units=0),
            coverage=CandidateCoverage(
                eligible_invoice_ids=["INV-70000"],
                considered_invoice_ids=["INV-70000"],
                status="complete",
            ),
        )
        target = self.updated_simulator if source_updated else self.simulator
        report = target.validate(proposal, "stateful-run")
        expected_codes = set()
        if stale or source_updated:
            expected_codes.add("stale_revision")
        if source_updated:
            expected_codes.add("invalid_evidence")
        assert {issue.code for issue in report.issues} == expected_codes
        if report.valid:
            self.validated_payload = {
                "proposal": proposal.model_dump(mode="json"),
                "report": report.model_dump(mode="json"),
            }

    @rule()
    def publish_replay_or_conflict(self) -> None:
        if self.validated_payload is None:
            return
        if self.published_payload is None:
            self.published_payload = deepcopy(self.validated_payload)
        self.store.save_artifact(
            "stateful-package",
            "stateful-run",
            1,
            "review_package",
            self.published_payload,
            "digest-1",
        )
        self.store.save_artifact(
            "stateful-package",
            "stateful-run",
            1,
            "review_package",
            self.published_payload,
            "digest-1",
        )
        try:
            self.store.save_artifact(
                "stateful-package",
                "stateful-run",
                1,
                "review_package",
                {"changed": True},
                "digest-2",
            )
        except ValueError as error:
            assert str(error) == "artifact_id_conflict"
        else:
            raise AssertionError("publication conflict must be rejected")

    def teardown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    @invariant()
    def ledger_is_immutable(self) -> None:
        assert self.simulator.observe() == self.initial
        assert self.updated_simulator.observe().invoices == self.initial.invoices


TestReadOnlyAccountingMachine = ReadOnlyAccountingMachine.TestCase
TestReadOnlyAccountingMachine.settings = settings(
    max_examples=20, stateful_step_count=12, deadline=None, derandomize=True
)


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
    fixture["snapshot"]["adjustments"][0]["source"]["value"] = status
    fixture["documents"][2]["spans"]["status"]["value"] = status
    fixture["documents"][2]["spans"]["status"]["text"] = f"status={status}"
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
    receipt_evidence = simulator.issued_evidence_ref(observation.evidence_id, "receipt")
    invoice_observation = simulator.read_document(tenant, "DOC-INVOICE", "property-run", "call-2")
    invoice_evidence = simulator.issued_evidence_ref(invoice_observation.evidence_id, "balance")
    adjustment = simulator.read_adjustment_history(tenant, "INV-70000", "property-run", "call-3")
    adjustment_evidence = adjustment["issued_refs"][0]
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
                evidence=[receipt_evidence, invoice_evidence, adjustment_evidence],
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


def test_inconsistent_adjustment_source_is_rejected_at_fixture_load(tmp_path) -> None:
    # 回帰: statusだけをapprovedへ変え、source本文がcancelledの矛盾fixtureを正解にしない。
    source = Path(__file__).parents[1] / "fixtures" / "accounting_known.json"
    fixture = json.loads(source.read_text(encoding="utf-8"))
    fixture["snapshot"]["adjustments"][0]["status"] = "approved"
    path = tmp_path / "inconsistent.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    try:
        AccountingSimulator(path)
    except ValueError as error:
        assert "invalid_fixture_adjustment_provenance" in str(error)
    else:
        raise AssertionError("inconsistent fixture must be rejected")
