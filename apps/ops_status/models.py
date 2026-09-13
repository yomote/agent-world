from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator


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


class RuntimeCapacitySnapshot(BaseModel):
    """公開が許可されたruntime集計だけを保持する。"""

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=240)
    observed_at: AwareDatetime
    state_source: Literal["runtime-list-agents-metadata"] | None = None
    limit_source: Literal["runtime-instructions"] | None = None
    running: int | None = Field(default=None, ge=0)
    idle: int | None = Field(default=None, ge=0)
    completed: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    max_concurrent_agents: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_sources_and_counts(self) -> "RuntimeCapacitySnapshot":
        state_counts = (self.running, self.idle, self.completed, self.total)
        if any(value is not None for value in state_counts) != (self.state_source is not None):
            raise ValueError("state counts and state_source must be supplied together")
        if (self.max_concurrent_agents is not None) != (self.limit_source is not None):
            raise ValueError("max_concurrent_agents and limit_source must be supplied together")
        if self.state_source is None and self.limit_source is None:
            raise ValueError("runtime capacity needs a state count or a concurrency limit")
        known_states = sum(
            value for value in (self.running, self.idle, self.completed) if value is not None
        )
        if self.total is not None and known_states > self.total:
            raise ValueError("known state counts cannot exceed total")
        if (
            self.running is not None
            and self.max_concurrent_agents is not None
            and self.running > self.max_concurrent_agents
        ):
            raise ValueError("running cannot exceed max_concurrent_agents")
        return self


class StatusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source: Literal["codex-event", "local-event-record", "ingest-upsert", "pm-confirmed", "fixture"]
    observed_at: AwareDatetime
    received_at: AwareDatetime
    items: list[WorkItem] = Field(max_length=32)
    runtime_capacity: RuntimeCapacitySnapshot | None = None


class StatusResponse(StatusSnapshot):
    stale: bool
    age_seconds: int


class StatusUpsertRequest(BaseModel):
    """ingestが明示した行とcapacityだけを既存snapshotへ反映する。"""

    model_config = ConfigDict(extra="forbid")

    source: Literal["local-event-record"]
    items: list[WorkItem] = Field(default_factory=list, max_length=32)
    runtime_capacity: RuntimeCapacitySnapshot | None = None

    @model_validator(mode="after")
    def check_targets(self) -> "StatusUpsertRequest":
        agents = [item.agent for item in self.items]
        if len(agents) != len(set(agents)):
            raise ValueError("upsert items must have unique agent identifiers")
        capacity_supplied = "runtime_capacity" in self.model_fields_set
        if capacity_supplied and self.runtime_capacity is None:
            raise ValueError("runtime_capacity cannot be null when supplied")
        if not self.items and not capacity_supplied:
            raise ValueError("upsert needs an item or runtime_capacity")
        return self


class StatusUpsertReceipt(BaseModel):
    """更新対象だけを返し、保持した他行をingestへ漏らさない。"""

    model_config = ConfigDict(extra="forbid")

    changed: bool
    revision: str = Field(min_length=1, max_length=256)
    items: list[WorkItem]
    runtime_capacity: RuntimeCapacitySnapshot | None = None
