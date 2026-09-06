from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    task: str = Field(min_length=1, max_length=240)
    status: Literal["running", "idle", "unknown", "review-wait", "blocked"]
    observed_at: AwareDatetime
    issue_url: HttpUrl | None = None
    pr_url: HttpUrl | None = None
    note: str | None = Field(default=None, max_length=500)


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
