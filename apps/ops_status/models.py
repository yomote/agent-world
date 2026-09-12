from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    task: str = Field(min_length=1, max_length=240)
    status: Literal[
        "not-started",
        "running",
        "review-wait",
        "human-wait",
        "stopped",
        "completed",
        "unknown",
        # v1 snapshots already stored in Azure remain readable.
        "idle",
        "blocked",
    ]
    observed_at: AwareDatetime
    issue_url: HttpUrl | None = None
    pr_url: HttpUrl | None = None
    note: str | None = Field(default=None, max_length=500)
    owner_label: str | None = Field(default=None, min_length=1, max_length=80)
    session_label: str | None = Field(default=None, min_length=1, max_length=80)
    task_label: str | None = Field(default=None, min_length=1, max_length=240)
    latest_activity: (
        Literal["session-created", "task-started", "task-complete", "structured-item"] | None
    ) = None
    latest_activity_at: AwareDatetime | None = None
    stale: bool = False
    current_action: str | None = Field(default=None, max_length=500)
    progress_summary: str | None = Field(default=None, max_length=500)
    summary_updated_at: AwareDatetime | None = None
    next_action: str | None = Field(default=None, max_length=500)
    blocker: str | None = Field(default=None, max_length=500)


class StatusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source: Literal["codex-event", "local-event-record", "pm-confirmed", "fixture"]
    observed_at: AwareDatetime
    received_at: AwareDatetime
    items: list[WorkItem] = Field(max_length=32)


class StatusResponse(StatusSnapshot):
    stale: bool
    age_seconds: int
