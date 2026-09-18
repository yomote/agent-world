from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from world.accounting_models import ReconciliationProposal, ValidationReport


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentDecision(StrictModel):
    kind: Literal["tool", "publish", "stop"]
    next_tool: str | None
    args_json: str
    proposal_json: str | None
    short_public_reason: str


class Question(StrictModel):
    question_id: str
    question: str
    reason: str
    options: list[str] = Field(min_length=2)


class ReviewPackage(StrictModel):
    artifact_id: str
    version: int
    status: Literal["ready_for_review"]
    run_id: str
    proposal: ReconciliationProposal
    validation: ValidationReport
    unresolved: list[str]
    sales_inquiry: list[str]
    source_provenance: list[dict]
    actual_ledger_updated: Literal[False] = False


class StartRunRequest(StrictModel):
    mode: Literal["agent", "baseline"] = "agent"


class AnswerRequest(StrictModel):
    action_id: str
    expected_step_version: int = Field(ge=0)
    question_id: str
    answer: str


class AdvanceRequest(StrictModel):
    action_id: str
    expected_step_version: int = Field(ge=0)


class RunView(StrictModel):
    run_id: str
    mode: str
    status: str
    fixture_id: str
    model_attempts: int
    model_successes: int
    model_failures: int
    step_version: int
    tool_calls: int
    question_count: int
    proposal_count: int
    deadline_at: str
    created_at: str
    trace: list[dict]
    artifact: dict | None
    pending_question: Question | None = None
