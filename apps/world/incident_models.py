"""障害復旧Labの公開契約。真因や期待解は含めない。"""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IncidentValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IncidentSummary(IncidentValue):
    run_id: UUID
    revision: int
    accepted: int
    incomplete: int
    completed: int
    duplicate_count: int
    state: Literal[
        "investigating", "waiting_approval", "applying", "verifying", "recovered", "stopped"
    ]


class EvidenceItem(IncidentValue):
    ref: str
    source: str
    timestamp: str
    data: dict[str, Any]


class IncidentToolRequest(IncidentValue):
    tool: Literal[
        "observe_system",
        "query_events",
        "inspect_queue",
        "read_service_config",
        "list_changes",
        "search_knowledge",
        "probe_dependency",
        "advance_and_verify",
        "lookup_action_status",
    ]
    args: dict[str, Any] = Field(default_factory=dict)


class IncidentToolResult(IncidentValue):
    tool: str
    status: Literal["success", "failure", "denied"]
    summary: IncidentSummary
    evidence: Annotated[tuple[EvidenceItem, ...], Field(max_length=40)]
    error: str | None = None


class RecoveryChange(IncidentValue):
    operation: Literal["set_config", "select_deployment", "requeue"]
    service: Literal["worker", "carrier", "queue"]
    key: str | None = None
    value: str | None = None
    order_ids: tuple[str, ...] = ()
    expected_state: str | None = None


class RecoveryProposal(IncidentValue):
    expected_revision: int
    diagnosis: Annotated[str, Field(min_length=1, max_length=500)]
    evidence_refs: Annotated[tuple[str, ...], Field(min_length=1, max_length=20)]
    changes: Annotated[tuple[RecoveryChange, ...], Field(min_length=1, max_length=4)]
    rollback: Annotated[str, Field(min_length=1, max_length=500)]
    verification_ticks: Literal[4] = 4
    canary_count: Literal[2] = 2
    verification_scope: Literal["affected_orders_and_canaries"] = "affected_orders_and_canaries"


class ExecutionRequest(IncidentValue):
    action_id: UUID
    proposal: RecoveryProposal


class ProposalResult(IncidentValue):
    status: Literal[
        "valid", "invalid", "waiting_approval", "approved", "applied", "failure", "stale"
    ]
    proposal_hash: str
    summary: IncidentSummary
    violations: tuple[str, ...] = ()
    event_refs: tuple[str, ...] = ()


class ApprovalRequest(IncidentValue):
    action_id: UUID
    proposal_hash: str
    expected_revision: int
    approve: bool


class ApplyRequest(IncidentValue):
    action_id: UUID
    proposal_hash: str
    expected_revision: int


class IncidentRunRequest(IncidentValue):
    scenario_number: Annotated[int, Field(ge=1, le=6)]


class IncidentSnapshot(IncidentValue):
    scenario_number: int
    summary: IncidentSummary
    services: dict[str, dict[str, Any]]
    evidence: tuple[EvidenceItem, ...]
    pending_proposal: RecoveryProposal | None = None
    proposal_hash: str | None = None
    approved_hash: str | None = None
    applied_changes: tuple[RecoveryChange, ...] = ()
