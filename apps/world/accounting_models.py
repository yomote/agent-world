from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoneyJPY(StrictModel):
    currency: Literal["JPY"] = "JPY"
    minor_units: int = Field(ge=0)


class SourceSpan(StrictModel):
    artifact_id: str
    version: int = Field(ge=1)
    span: str
    observed_at: datetime


class AccountingDocument(StrictModel):
    document_id: str
    tenant_id: str
    version: int = Field(ge=1)
    kind: Literal["bank_statement", "invoice", "remittance", "email", "adjustment"]
    title: str
    body: str
    spans: dict[str, str]


class Invoice(StrictModel):
    invoice_id: str
    tenant_id: str
    counterparty_id: str
    amount: MoneyJPY
    open_amount: MoneyJPY
    issued_on: str


class Receipt(StrictModel):
    receipt_id: str
    tenant_id: str
    amount: MoneyJPY
    booked_on: str
    payer_text: str


class Adjustment(StrictModel):
    adjustment_id: str
    tenant_id: str
    invoice_id: str
    amount: MoneyJPY
    status: Literal["draft", "approved", "cancelled", "applied"]
    source: SourceSpan


class AccountingSnapshot(StrictModel):
    world_id: str
    revision: int = Field(ge=0)
    tenant_id: str
    as_of: datetime
    invoices: list[Invoice]
    receipts: list[Receipt]
    adjustments: list[Adjustment]


class AllocationLine(StrictModel):
    receipt_id: str
    invoice_id: str
    cash_amount: MoneyJPY
    adjustment_candidate: MoneyJPY | None = None
    evidence: list[SourceSpan] = Field(min_length=1)


class CandidateCoverage(StrictModel):
    eligible_invoice_ids: list[str]
    considered_invoice_ids: list[str]
    status: Literal["complete", "incomplete", "access_limited"]


class ReconciliationProposal(StrictModel):
    proposal_id: str
    version: int = Field(ge=1)
    world_id: str
    based_on_revision: int = Field(ge=0)
    tenant_id: str
    receipt_id: str
    allocations: list[AllocationLine]
    unapplied: MoneyJPY
    coverage: CandidateCoverage
    questions: list[str] = []


class ValidationIssue(StrictModel):
    code: Literal[
        "stale_revision",
        "scope_violation",
        "unknown_reference",
        "invalid_evidence",
        "cancelled_adjustment",
        "duplicate_allocation",
        "over_allocation",
        "incomplete_coverage",
    ]
    detail: str


class ValidationReport(StrictModel):
    valid: bool
    world_id: str
    world_revision: int
    receipt_amount: MoneyJPY
    allocated_cash: MoneyJPY
    unapplied: MoneyJPY
    planned_balance: MoneyJPY
    ledger_balance_unchanged: bool
    issues: list[ValidationIssue]

    @model_validator(mode="after")
    def valid_matches_issues(self) -> "ValidationReport":
        if self.valid == bool(self.issues):
            raise ValueError("valid must be true exactly when issues is empty")
        return self


class ToolObservation(StrictModel):
    evidence_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    world_revision: int
    payload: dict
