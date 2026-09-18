from copy import deepcopy
from hashlib import sha256
from json import dumps
from pathlib import Path
from typing import Any

from .accounting_models import (
    AccountingDocument,
    AccountingSnapshot,
    AllocationLine,
    EvidenceRef,
    MoneyJPY,
    ReconciliationProposal,
    ToolObservation,
    ValidationIssue,
    ValidationReport,
)


class AccountingDomainError(ValueError):
    pass


class AccountingSimulator:
    """Authoritative read-only accounting snapshot and deterministic domain tools."""

    def __init__(self, fixture_path: Path | None = None) -> None:
        path = fixture_path or Path(__file__).with_name("fixtures") / "accounting_known.json"
        fixture_bytes = path.read_bytes()
        self.fixture_id = f"fixture-{sha256(fixture_bytes).hexdigest()[:12]}"
        raw = __import__("json").loads(fixture_bytes.decode("utf-8"))
        self._snapshot = AccountingSnapshot.model_validate(raw["snapshot"])
        self._documents = {
            item.document_id: item
            for item in (AccountingDocument.model_validate(value) for value in raw["documents"])
        }
        self._aliases: list[dict[str, str]] = raw["aliases"]
        self._issued_evidence: dict[str, tuple[str, EvidenceRef]] = {}
        self._initial_open_totals = {
            tenant: self._open_total(tenant)
            for tenant in {item.tenant_id for item in self._snapshot.invoices}
        }
        self._validate_fixture_provenance()

    def observe(self) -> AccountingSnapshot:
        return self._snapshot.model_copy(deep=True)

    def list_documents(self, tenant_id: str) -> list[dict[str, Any]]:
        self._require_tenant(tenant_id)
        return [
            {
                "document_id": item.document_id,
                "version": item.version,
                "kind": item.kind,
                "title": item.title,
            }
            for item in self._documents.values()
            if item.tenant_id == tenant_id
        ]

    def search_documents(self, tenant_id: str, query: str) -> list[dict[str, Any]]:
        self._require_tenant(tenant_id)
        normalized = query.casefold().strip()
        return [
            {"document_id": item.document_id, "version": item.version, "title": item.title}
            for item in self._documents.values()
            if item.tenant_id == tenant_id
            and normalized
            and normalized in f"{item.title}\n{item.body}".casefold()
        ]

    def read_document(
        self, tenant_id: str, document_id: str, run_id: str, tool_call_id: str
    ) -> ToolObservation:
        self._require_tenant(tenant_id)
        document = self._documents.get(document_id)
        if document is None or document.tenant_id != tenant_id:
            raise AccountingDomainError("unknown_document")
        payload = document.model_dump(mode="json")
        evidence_id = self._evidence_id(run_id, tool_call_id, payload)
        for span in document.spans:
            metadata = document.spans[span]
            ref = EvidenceRef(
                artifact_id=document.document_id,
                version=document.version,
                span=span,
                observed_at=self._snapshot.as_of,
                fact_type=metadata.fact_type,
                subject_id=metadata.subject_id,
                value=metadata.value,
            )
            self._issued_evidence[f"{evidence_id}:{span}"] = (run_id, ref)
        return ToolObservation(
            evidence_id=evidence_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name="read_document",
            world_revision=self._snapshot.revision,
            payload=payload,
        )

    def read_open_receivables(self, tenant_id: str) -> dict[str, Any]:
        self._require_tenant(tenant_id)
        return {
            "world_id": self._snapshot.world_id,
            "revision": self._snapshot.revision,
            "as_of": self._snapshot.as_of.isoformat(),
            "invoices": [
                item.model_dump(mode="json")
                for item in self._snapshot.invoices
                if item.tenant_id == tenant_id
            ],
            "receipts": [
                item.model_dump(mode="json")
                for item in self._snapshot.receipts
                if item.tenant_id == tenant_id
            ],
        }

    def lookup_counterparty(self, tenant_id: str, text: str) -> dict[str, Any]:
        self._require_tenant(tenant_id)
        matches = [
            item["counterparty_id"]
            for item in self._aliases
            if item["tenant_id"] == tenant_id and item["text"].casefold() == text.casefold()
        ]
        return {
            "query": text,
            "confirmed_ids": matches if len(matches) == 1 else [],
            "candidates": matches,
        }

    def read_adjustment_history(
        self, tenant_id: str, invoice_id: str, run_id: str, tool_call_id: str
    ) -> dict[str, Any]:
        self._require_tenant(tenant_id)
        history = [
            item.model_dump(mode="json")
            for item in self._snapshot.adjustments
            if item.invoice_id == invoice_id and item.tenant_id == tenant_id
        ]
        evidence_id = self._evidence_id(run_id, tool_call_id, {"history": history})
        issued_refs = []
        for item in self._snapshot.adjustments:
            if item.invoice_id == invoice_id and item.tenant_id == tenant_id:
                self._issued_evidence[f"{evidence_id}:{item.source.span}"] = (
                    run_id,
                    item.source,
                )
                issued_refs.append(item.source.model_dump(mode="json"))
        return {"history": history, "evidence_id": evidence_id, "issued_refs": issued_refs}

    def find_allocation_candidates(
        self, tenant_id: str, receipt_id: str, invoice_ids: list[str]
    ) -> dict[str, Any]:
        self._require_tenant(tenant_id)
        receipt = self._receipt(tenant_id, receipt_id)
        eligible = sorted(
            item.invoice_id
            for item in self._snapshot.invoices
            if item.tenant_id == tenant_id and item.open_amount.minor_units > 0
        )
        considered = sorted(set(invoice_ids))
        unknown = sorted(set(considered) - set(eligible))
        if unknown:
            raise AccountingDomainError(f"unknown_or_ineligible_invoice:{','.join(unknown)}")
        invoices = {
            item.invoice_id: item for item in self._snapshot.invoices if item.tenant_id == tenant_id
        }
        selected_total = sum(invoices[item].open_amount.minor_units for item in considered)
        options = [
            {
                "invoice_ids": [item],
                "cash_allocations": {item: receipt.amount.minor_units},
                "kind": "partial_or_exact_single",
            }
            for item in considered
            if invoices[item].open_amount.minor_units >= receipt.amount.minor_units
        ]
        return {
            "receipt_id": receipt_id,
            "receipt_amount": receipt.amount.model_dump(),
            "considered_invoice_ids": considered,
            "eligible_invoice_ids": eligible,
            "selected_open_total": MoneyJPY(minor_units=selected_total).model_dump(),
            "difference": selected_total - receipt.amount.minor_units,
            "coverage": "complete" if considered == eligible else "incomplete",
            "allocation_options": options,
        }

    def validate(self, proposal: ReconciliationProposal, run_id: str) -> ValidationReport:
        issues: list[ValidationIssue] = []
        if (
            proposal.world_id != self._snapshot.world_id
            or proposal.based_on_revision != self._snapshot.revision
        ):
            issues.append(
                ValidationIssue(code="stale_revision", detail="World snapshotが更新されています")
            )
        if proposal.tenant_id != self._snapshot.tenant_id:
            issues.append(
                ValidationIssue(code="scope_violation", detail="tenant scopeが一致しません")
            )
        try:
            receipt = self._receipt(proposal.tenant_id, proposal.receipt_id)
        except AccountingDomainError:
            issues.append(ValidationIssue(code="unknown_reference", detail="receiptが存在しません"))
            receipt_amount = 0
        else:
            receipt_amount = receipt.amount.minor_units
        invoices = {
            item.invoice_id: item
            for item in self._snapshot.invoices
            if item.tenant_id == proposal.tenant_id
        }
        ambiguous_invoice_ids = {
            item.invoice_id
            for item in invoices.values()
            if item.open_amount.minor_units >= receipt_amount
        }
        seen: set[tuple[str, str]] = set()
        allocated = 0
        for line in proposal.allocations:
            key = (line.receipt_id, line.invoice_id)
            if key in seen:
                issues.append(
                    ValidationIssue(
                        code="duplicate_allocation", detail=f"重複行: {line.invoice_id}"
                    )
                )
            seen.add(key)
            invoice = invoices.get(line.invoice_id)
            if (
                invoice is None
                or invoice.tenant_id != proposal.tenant_id
                or line.receipt_id != proposal.receipt_id
            ):
                issues.append(
                    ValidationIssue(
                        code="unknown_reference", detail=f"不正な参照: {line.invoice_id}"
                    )
                )
                continue
            if line.cash_amount.minor_units > invoice.open_amount.minor_units:
                issues.append(
                    ValidationIssue(code="over_allocation", detail=f"残高超過: {line.invoice_id}")
                )
            allocated += line.cash_amount.minor_units
            self._validate_evidence(line, run_id, issues)
            facts = {(ref.fact_type, ref.subject_id, ref.value) for ref in line.evidence}
            receipt_fact = (
                "receipt_amount",
                proposal.receipt_id,
                str(receipt_amount),
            )
            invoice_fact = (
                "invoice_open_amount",
                line.invoice_id,
                str(invoice.open_amount.minor_units),
            )
            if receipt_fact not in facts or invoice_fact not in facts:
                issues.append(
                    ValidationIssue(
                        code="invalid_evidence",
                        detail=f"入金額または請求残高の根拠不足: {line.invoice_id}",
                    )
                )
            if (
                len(ambiguous_invoice_ids) > 1
                and (
                    "allocation_link",
                    proposal.receipt_id,
                    line.invoice_id,
                )
                not in facts
            ):
                issues.append(
                    ValidationIssue(
                        code="invalid_evidence",
                        detail=f"同額候補を特定する対応づけ根拠不足: {line.invoice_id}",
                    )
                )
            if line.adjustment_candidate and line.adjustment_candidate.minor_units:
                statuses = {
                    item.status
                    for item in self._snapshot.adjustments
                    if item.invoice_id == line.invoice_id
                    and item.tenant_id == proposal.tenant_id
                    and item.amount.minor_units == line.adjustment_candidate.minor_units
                }
                if statuses != {"approved"}:
                    issues.append(
                        ValidationIssue(
                            code="cancelled_adjustment",
                            detail=f"取消済みまたは未確認の調整: {line.invoice_id}",
                        )
                    )
                adjustment_evidence = {
                    (ref.fact_type, ref.value)
                    for ref in line.evidence
                    if ref.subject_id
                    in {
                        item.adjustment_id
                        for item in self._snapshot.adjustments
                        if item.invoice_id == line.invoice_id
                        and item.tenant_id == proposal.tenant_id
                    }
                }
                if ("adjustment_status", "approved") not in adjustment_evidence:
                    issues.append(
                        ValidationIssue(
                            code="invalid_evidence",
                            detail=f"承認済み調整の観測根拠不足: {line.invoice_id}",
                        )
                    )
        eligible = sorted(
            item.invoice_id
            for item in self._snapshot.invoices
            if item.tenant_id == proposal.tenant_id and item.open_amount.minor_units > 0
        )
        if (
            proposal.coverage.status != "complete"
            or sorted(proposal.coverage.eligible_invoice_ids) != eligible
            or sorted(proposal.coverage.considered_invoice_ids) != eligible
        ):
            issues.append(
                ValidationIssue(code="incomplete_coverage", detail="適格候補の探索が不完全です")
            )
        if allocated + proposal.unapplied.minor_units != receipt_amount:
            issues.append(
                ValidationIssue(code="over_allocation", detail="入金額と配分+未配分が一致しません")
            )
        return ValidationReport(
            valid=not issues,
            world_id=self._snapshot.world_id,
            world_revision=self._snapshot.revision,
            receipt_amount=MoneyJPY(minor_units=receipt_amount),
            allocated_cash=MoneyJPY(minor_units=allocated),
            unapplied=proposal.unapplied,
            planned_balance=MoneyJPY(
                minor_units=max(0, self._open_total(proposal.tenant_id) - allocated)
            ),
            ledger_balance_unchanged=(
                self._open_total(proposal.tenant_id)
                == self._initial_open_totals.get(proposal.tenant_id, 0)
            ),
            issues=issues,
        )

    def issued_evidence_ref(self, evidence_id: str, span: str) -> EvidenceRef:
        try:
            return self._issued_evidence[f"{evidence_id}:{span}"][1].model_copy(deep=True)
        except KeyError as error:
            raise AccountingDomainError("evidence_not_issued") from error

    def restore_evidence(self, run_id: str, refs: list[dict[str, Any]]) -> None:
        """Restores only refs persisted in this run's tool receipts after process restart."""
        for raw in refs:
            ref = EvidenceRef.model_validate(raw)
            if not self._is_current_evidence(ref):
                continue
            key = f"restored:{ref.artifact_id}:{ref.version}:{ref.span}"
            self._issued_evidence[key] = (run_id, ref)

    def _is_current_evidence(self, ref: EvidenceRef) -> bool:
        document = self._documents.get(ref.artifact_id)
        if document and document.version == ref.version and ref.span in document.spans:
            span = document.spans[ref.span]
            return (
                span.fact_type == ref.fact_type
                and span.subject_id == ref.subject_id
                and span.value == ref.value
            )
        return any(item.source == ref for item in self._snapshot.adjustments)

    def _validate_fixture_provenance(self) -> None:
        for item in self._snapshot.adjustments:
            source = item.source
            if (
                source.fact_type != "adjustment_status"
                or source.subject_id != item.adjustment_id
                or source.value != item.status
                or not self._is_current_evidence(source)
            ):
                raise AccountingDomainError("invalid_fixture_adjustment_provenance")

    def _validate_evidence(
        self, line: AllocationLine, run_id: str, issues: list[ValidationIssue]
    ) -> None:
        for ref in line.evidence:
            candidates = [
                issued
                for issued_run, issued in self._issued_evidence.values()
                if issued_run == run_id and issued == ref
            ]
            if not candidates:
                issues.append(
                    ValidationIssue(
                        code="invalid_evidence",
                        detail=f"当runで観測されていない根拠: {ref.artifact_id}#{ref.span}",
                    )
                )

    def _receipt(self, tenant_id: str, receipt_id: str):
        for item in self._snapshot.receipts:
            if item.receipt_id == receipt_id and item.tenant_id == tenant_id:
                return item
        raise AccountingDomainError("unknown_receipt")

    def _require_tenant(self, tenant_id: str) -> None:
        if tenant_id != self._snapshot.tenant_id:
            raise AccountingDomainError("scope_violation")

    def _open_total(self, tenant_id: str) -> int:
        return sum(
            item.open_amount.minor_units
            for item in self._snapshot.invoices
            if item.tenant_id == tenant_id
        )

    @staticmethod
    def _evidence_id(run_id: str, tool_call_id: str, payload: dict[str, Any]) -> str:
        canonical = dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"ev-{sha256(f'{run_id}:{tool_call_id}:{canonical}'.encode()).hexdigest()[:16]}"

    def clone(self) -> "AccountingSimulator":
        return deepcopy(self)
