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
    parent_relation: Literal["root", "delegated", "unknown"] | None = None
    parent_agent: str | None = Field(default=None, min_length=1, max_length=80)
    parent_source: Literal["runtime-canonical-task-path", "explicit-delegation"] | None = None
    parent_observed_at: AwareDatetime | None = None
    instruction_summary: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check_parent_relation(self) -> "WorkItem":
        if self.instruction_summary is not None and self.summary_updated_at is None:
            raise ValueError("instruction_summary needs summary_updated_at")
        if self.parent_agent == self.agent:
            raise ValueError("agent cannot delegate to itself")
        if self.parent_relation is None:
            if any(
                value is not None
                for value in (self.parent_agent, self.parent_source, self.parent_observed_at)
            ):
                raise ValueError("parent fields need parent_relation")
            return self
        if self.parent_relation == "delegated":
            if any(
                value is None
                for value in (self.parent_agent, self.parent_source, self.parent_observed_at)
            ):
                raise ValueError("delegated parent needs agent, source, and observation time")
        elif self.parent_relation == "root":
            if (
                self.parent_agent is not None
                or self.parent_source is None
                or self.parent_observed_at is None
            ):
                raise ValueError("root parent needs source and observation time without an agent")
        elif any(
            value is not None
            for value in (self.parent_agent, self.parent_source, self.parent_observed_at)
        ):
            raise ValueError("unknown parent cannot include inferred parent fields")
        return self


class FocusSummary(BaseModel):
    """画面最上部へ表示する、明示された公開用の今回要約。"""

    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=500)
    progress_summary: str = Field(min_length=1, max_length=500)
    blocker: str | None = Field(default=None, max_length=500)
    next_action: str = Field(min_length=1, max_length=500)
    updated_at: AwareDatetime
    source: Literal["manual-public-summary"]


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
    available: int | None = Field(default=None, ge=0)
    availability_source: Literal["derived-running-limit"] | None = None
    availability_definition: Literal["max-concurrent-minus-running"] | None = None

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
        availability_fields = (
            self.available,
            self.availability_source,
            self.availability_definition,
        )
        if any(value is not None for value in availability_fields):
            if any(value is None for value in availability_fields):
                raise ValueError(
                    "availability value, source, and definition must be supplied together"
                )
            if self.running is None or self.max_concurrent_agents is None:
                raise ValueError("availability needs running and max_concurrent_agents")
            if self.available != self.max_concurrent_agents - self.running:
                raise ValueError("available must equal max_concurrent_agents minus running")
        return self


class SessionTreeNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1, max_length=80)
    parent_agent: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def check_self_parent(self) -> "SessionTreeNode":
        if self.agent == self.parent_agent:
            raise ValueError("session tree node cannot parent itself")
        return self


class SessionTreeSnapshot(BaseModel):
    """current sessionの全nodeと公開row coverageを明示する。"""

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=240)
    observed_at: AwareDatetime
    source: Literal["runtime-list-agents-metadata"]
    root_agent: str = Field(min_length=1, max_length=80)
    nodes: list[SessionTreeNode] = Field(min_length=1, max_length=32)
    covered_agents: list[str] = Field(max_length=32)

    @model_validator(mode="after")
    def check_tree(self) -> "SessionTreeSnapshot":
        by_agent = {node.agent: node for node in self.nodes}
        if len(by_agent) != len(self.nodes):
            raise ValueError("session tree nodes must be unique")
        if len(set(self.covered_agents)) != len(self.covered_agents):
            raise ValueError("covered agents must be unique")
        root = by_agent.get(self.root_agent)
        if root is None or root.parent_agent is not None:
            raise ValueError("session tree root must exist without a parent")
        if any(agent not in by_agent for agent in self.covered_agents):
            raise ValueError("covered agents must belong to the session tree")
        for node in self.nodes:
            if node.agent != self.root_agent and node.parent_agent not in by_agent:
                raise ValueError("every non-root node needs a parent in the session tree")
            seen: set[str] = set()
            current: str | None = node.agent
            while current is not None:
                if current in seen:
                    raise ValueError("session tree cannot contain a cycle")
                seen.add(current)
                current = by_agent[current].parent_agent
        return self


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1, max_length=80)
    parent_agent: str | None = Field(default=None, min_length=1, max_length=80)
    status: Literal["completed"]
    last_observed_at: AwareDatetime | None = None
    source: Literal["runtime-list-agents-metadata", "pm-recorded-completed-work-unit"]

    @model_validator(mode="after")
    def check_self_parent(self) -> "HistoryEntry":
        if self.agent == self.parent_agent:
            raise ValueError("known history entry cannot parent itself")
        return self


class KnownHistorySnapshot(BaseModel):
    """current inventoryとは分離した、明示済みの過去work unit。"""

    model_config = ConfigDict(extra="forbid")

    recorded_at: AwareDatetime
    entries: list[HistoryEntry] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def check_unique_agents(self) -> "KnownHistorySnapshot":
        agents = [entry.agent for entry in self.entries]
        if len(agents) != len(set(agents)):
            raise ValueError("known history agents must be unique")
        parents = {entry.agent: entry.parent_agent for entry in self.entries}
        for start in parents:
            seen: set[str] = set()
            current: str | None = start
            while current in parents:
                if current in seen:
                    raise ValueError("known history cannot contain a cycle")
                seen.add(current)
                current = parents[current]
        return self


class StatusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source: Literal["codex-event", "local-event-record", "ingest-upsert", "pm-confirmed", "fixture"]
    observed_at: AwareDatetime
    received_at: AwareDatetime
    items: list[WorkItem] = Field(max_length=32)
    runtime_capacity: RuntimeCapacitySnapshot | None = None
    focus_summary: FocusSummary | None = None
    session_tree: SessionTreeSnapshot | None = None
    known_history: KnownHistorySnapshot | None = None

    @model_validator(mode="after")
    def check_parent_cycles(self) -> "StatusSnapshot":
        parents = {
            item.agent: item.parent_agent
            for item in self.items
            if item.parent_relation == "delegated" and item.parent_agent is not None
        }
        for start in parents:
            seen: set[str] = set()
            current: str | None = start
            while current in parents:
                if current in seen:
                    raise ValueError("parent relation cannot contain a cycle")
                seen.add(current)
                current = parents[current]
        if self.session_tree is not None:
            item_agents = {item.agent for item in self.items}
            if any(agent not in item_agents for agent in self.session_tree.covered_agents):
                raise ValueError("covered session tree agents need public work items")
        return self


class StatusResponse(StatusSnapshot):
    stale: bool
    age_seconds: int


class StatusUpsertRequest(BaseModel):
    """ingestが明示した行とcapacityだけを既存snapshotへ反映する。"""

    model_config = ConfigDict(extra="forbid")

    source: Literal["local-event-record"]
    items: list[WorkItem] = Field(default_factory=list, max_length=32)
    runtime_capacity: RuntimeCapacitySnapshot | None = None
    focus_summary: FocusSummary | None = None
    session_tree: SessionTreeSnapshot | None = None
    known_history: KnownHistorySnapshot | None = None

    @model_validator(mode="after")
    def check_targets(self) -> "StatusUpsertRequest":
        agents = [item.agent for item in self.items]
        if len(agents) != len(set(agents)):
            raise ValueError("upsert items must have unique agent identifiers")
        capacity_supplied = "runtime_capacity" in self.model_fields_set
        if capacity_supplied and self.runtime_capacity is None:
            raise ValueError("runtime_capacity cannot be null when supplied")
        focus_supplied = "focus_summary" in self.model_fields_set
        if focus_supplied and self.focus_summary is None:
            raise ValueError("focus_summary cannot be null when supplied")
        tree_supplied = "session_tree" in self.model_fields_set
        if tree_supplied and self.session_tree is None:
            raise ValueError("session_tree cannot be null when supplied")
        history_supplied = "known_history" in self.model_fields_set
        if history_supplied and self.known_history is None:
            raise ValueError("known_history cannot be null when supplied")
        if (
            not self.items
            and not capacity_supplied
            and not focus_supplied
            and not tree_supplied
            and not history_supplied
        ):
            raise ValueError(
                "upsert needs an item, runtime_capacity, focus_summary, or session_tree"
            )
        for item in self.items:
            if (item.latest_activity is None) != (item.latest_activity_at is None):
                raise ValueError("upsert activity and its timestamp must be supplied together")
            has_summary = any(
                value is not None
                for value in (
                    item.current_action,
                    item.progress_summary,
                    item.instruction_summary,
                )
            )
            if has_summary != (item.summary_updated_at is not None):
                raise ValueError("upsert summary and its timestamp must be supplied together")
        return self


class StatusUpsertReceipt(BaseModel):
    """更新対象だけを返し、保持した他行をingestへ漏らさない。"""

    model_config = ConfigDict(extra="forbid")

    changed: bool
    revision: str = Field(min_length=1, max_length=256)
    items: list[WorkItem]
    runtime_capacity: RuntimeCapacitySnapshot | None = None
    focus_summary: FocusSummary | None = None
    session_tree: SessionTreeSnapshot | None = None
    known_history: KnownHistorySnapshot | None = None
