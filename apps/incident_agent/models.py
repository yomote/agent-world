from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentValue(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelDecision(AgentValue):
    kind: Literal["tool", "propose", "stop"]
    next_tool: str | None
    args_json: str
    proposal_json: str | None
    short_public_reason: str
    evidence_refs: list[str]


class TraceEntry(AgentValue):
    sequence: int
    kind: Literal["model", "tool", "proposal", "approval", "verification", "failure"]
    name: str
    public_reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRun(AgentValue):
    run_id: str
    scenario_number: int
    status: Literal[
        "observing",
        "investigating",
        "waiting_human",
        "proposing",
        "waiting_approval",
        "applying",
        "verifying",
        "recovered",
        "stopped",
        "unknown",
    ]
    mode: Literal["actual", "baseline"]
    provider: str
    model: str
    model_calls: int
    model_successes: int
    model_failures: int
    global_model_calls_remaining: int | None
    tool_calls: int
    clarification_count: int
    proposal_count: int
    latest_reason: str
    trace: list[TraceEntry]
    world: dict[str, Any]
    pending_question: dict[str, Any] | None = None
    pending_proposal: dict[str, Any] | None = None
    proposal_hash: str | None = None
    execution_action_id: str | None = None
    verification_action_id: str | None = None
    reconciliation_action_id: str | None = None
    artifacts: dict[str, Any]
    error: str | None = None


class StartAgentRequest(AgentValue):
    scenario_number: int = Field(ge=1, le=6)
    mode: Literal["actual", "baseline"] = "actual"


class HumanAnswer(AgentValue):
    answer: Literal["use_spare", "wait_primary"]


class HumanApproval(AgentValue):
    approve: bool
    approver: str = Field(min_length=1, max_length=64)
