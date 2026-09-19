import hashlib
import json
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


class RequestEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["issue", "pull-request", "review", "deployment"]
    url: HttpUrl
    observed_at: AwareDatetime


class RequestRecord(BaseModel):
    """Issue/PRを正本として参照する、公開可能な依頼運用索引。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    scope_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    issue_url: HttpUrl
    issue_state: Literal["open", "closed", "unknown"]
    issue_observation: Literal["confirmed", "unavailable"]
    issue_observed_at: AwareDatetime
    public_title: str = Field(min_length=1, max_length=240)
    public_purpose: str = Field(min_length=1, max_length=500)
    acceptance_summary: str = Field(min_length=1, max_length=500)
    authority_source: Literal["github-issue-observation"]
    lifecycle: Literal[
        "registered",
        "delegated",
        "running",
        "blocked",
        "handover-waiting",
        "reconnectable",
        "completed",
    ]
    owner_agent: str | None = Field(default=None, min_length=1, max_length=80)
    member_agents: list[str] = Field(default_factory=list, max_length=32)
    progress_summary: str | None = Field(default=None, max_length=500)
    blocker: str | None = Field(default=None, max_length=500)
    next_action: str | None = Field(default=None, max_length=500)
    report_updated_at: AwareDatetime
    report_source: Literal["manual-public-summary"]
    runtime_connection: Literal["connected", "record-only", "unknown"]
    runtime_observed_at: AwareDatetime | None = None
    evidence: list[RequestEvidence] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def check_connection_and_identity(self) -> "RequestRecord":
        if len(self.member_agents) != len(set(self.member_agents)):
            raise ValueError("request member agents must be unique")
        if (self.runtime_connection == "unknown") != (self.runtime_observed_at is None):
            raise ValueError("known runtime connection needs its own observation time")
        if self.lifecycle == "completed" and self.issue_state != "closed":
            raise ValueError("completed request needs a closed Issue observation")
        if (self.issue_state == "unknown") != (self.issue_observation == "unavailable"):
            raise ValueError("unknown Issue state needs an unavailable observation marker")
        return self


class FrontDeskClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    claimed_at: AwareDatetime
    claim_generation: int | None = Field(default=None, ge=1)
    runtime_session_id: str | None = Field(default=None, min_length=1, max_length=200)
    runtime_observed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def check_runtime_identity(self) -> "FrontDeskClaim":
        if (self.runtime_session_id is None) != (self.runtime_observed_at is None):
            raise ValueError("runtime session identity needs its own observation time")
        return self


class StatusRuntimeBinding(BaseModel):
    """runtime由来statusをactive Front Desk claimへ結び付ける。"""

    model_config = ConfigDict(extra="forbid")

    registry_generation: int = Field(ge=1)
    front_desk_alias: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    runtime_session_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RegistryHandover(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ready", "accepted"]
    from_front_desk: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    to_front_desk: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    prepared_at: AwareDatetime
    accepted_at: AwareDatetime | None = None
    resume_policy: Literal["explicit-dispatch-required"]

    @model_validator(mode="after")
    def check_state(self) -> "RegistryHandover":
        if self.from_front_desk == self.to_front_desk:
            raise ValueError("handover needs a different successor")
        if (self.state == "accepted") != (self.accepted_at is not None):
            raise ValueError("accepted handover needs accepted_at only after claim")
        return self


class RequestRegistrySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: int = Field(ge=1)
    active_front_desk: FrontDeskClaim
    updated_at: AwareDatetime
    source: Literal["manual-public-registry"]
    requests: list[RequestRecord] = Field(min_length=1, max_length=32)
    handover: RegistryHandover | None = None

    @model_validator(mode="after")
    def check_requests(self) -> "RequestRegistrySnapshot":
        ids = [request.request_id for request in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("registry request identifiers must be unique")
        scopes = [request.scope_id for request in self.requests]
        if len(scopes) != len(set(scopes)):
            raise ValueError("registry request scope identifiers must be unique")
        return self


class RequestRegistryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["manual-public-registry"]
    action: Literal["initialize", "update", "prepare-handover", "claim-handover"]
    expected_generation: int = Field(ge=0)
    actor_front_desk: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    actor_runtime_session_id: str | None = Field(default=None, min_length=1, max_length=200)
    observed_at: AwareDatetime
    requests: list[RequestRecord] = Field(default_factory=list, max_length=32)
    successor_front_desk: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    bundle_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def check_action(self) -> "RequestRegistryUpdate":
        ids = [request.request_id for request in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("registry update request identifiers must be unique")
        scopes = [request.scope_id for request in self.requests]
        if len(scopes) != len(set(scopes)):
            raise ValueError("registry update scope identifiers must be unique")
        needs_handover = self.action in {"prepare-handover", "claim-handover"}
        if needs_handover != (
            self.successor_front_desk is not None and self.bundle_digest is not None
        ):
            raise ValueError("handover action needs successor and bundle digest")
        if self.action == "initialize" and (self.expected_generation != 0 or not self.requests):
            raise ValueError("initialize needs generation zero and at least one request")
        if self.action == "update" and not self.requests:
            raise ValueError("update needs at least one request")
        if needs_handover and self.requests:
            raise ValueError("handover actions cannot update requests")
        if self.action == "claim-handover" and self.actor_runtime_session_id is None:
            raise ValueError("handover claim needs the new runtime session identity")
        if self.action in {"update", "prepare-handover"} and (
            self.actor_runtime_session_id is not None
        ):
            raise ValueError("runtime session identity is not accepted for this action")
        return self


class RequestRegistryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed: bool
    revision: str = Field(min_length=1, max_length=256)
    generation: int = Field(ge=1)
    active_front_desk: FrontDeskClaim | None = None
    requests: list[RequestRecord]
    handover: RegistryHandover | None = None


class PmTaskObservation(BaseModel):
    """Issue/PRとPM decisionを正本にする、claimを持たないread-only task観測。"""

    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=240)
    purpose: str = Field(min_length=1, max_length=500)
    acceptance_summary: str = Field(min_length=1, max_length=500)
    owner: str | None = Field(default=None, min_length=1, max_length=80)
    state: Literal[
        "not-started", "running", "review-wait", "blocked", "stopped", "completed", "unknown"
    ]
    current_step: str | None = Field(default=None, max_length=500)
    next_action: str | None = Field(default=None, max_length=500)
    resume_trigger: str | None = Field(default=None, max_length=500)
    blocker: str | None = Field(default=None, max_length=500)
    waiting_on: Literal["worker", "pm", "po", "external", "none"] | None = None
    waiting_detail: str | None = Field(default=None, min_length=1, max_length=240)
    issue_url: HttpUrl
    pr_url: HttpUrl | None = None
    source_version: str = Field(min_length=1, max_length=200)
    observed_at: AwareDatetime
    po_status: Literal["not-required", "pending", "accepted", "unknown"] = "unknown"
    evidence: list[RequestEvidence] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def check_waiting_owner(self) -> "PmTaskObservation":
        if self.waiting_on in {None, "none"} and self.waiting_detail is not None:
            raise ValueError("waiting detail needs a concrete waiting owner")
        if self.waiting_on not in {None, "none"} and self.waiting_detail is None:
            raise ValueError("concrete waiting owner needs a public detail")
        return self


class PmTaskProjection(BaseModel):
    """PMが確認したtask状態の再生成可能cache。control registryではない。"""

    model_config = ConfigDict(extra="forbid")
    source_kind: Literal["pm-observation"]
    source_version: str = Field(min_length=1, max_length=200)
    source_refs: list[HttpUrl] = Field(default_factory=list, max_length=16)
    content_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    observed_at: AwareDatetime
    tasks: list[PmTaskObservation] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def check_tasks(self) -> "PmTaskProjection":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("PM task identifiers must be unique")
        if any(task.observed_at > self.observed_at for task in self.tasks):
            raise ValueError("PM task observation cannot be newer than its projection")
        canonical = json.dumps(
            [task.model_dump(mode="json") for task in self.tasks],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        expected = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
        if self.content_digest is not None and self.content_digest != expected:
            raise ValueError("PM task projection content digest does not match its tasks")
        self.content_digest = expected
        return self


def validate_pm_task_projection_transition(
    previous: PmTaskProjection, incoming: PmTaskProjection
) -> None:
    """projection全体の時計でtask rowの退行・同clock差替えを隠さない。"""

    incoming_by_id = {task.task_id: task for task in incoming.tasks}
    for old_task in previous.tasks:
        new_task = incoming_by_id.get(old_task.task_id)
        if new_task is None:
            raise ValueError("PM task projection cannot silently remove a task")
        if new_task.observed_at < old_task.observed_at:
            raise ValueError("PM task observation cannot move backwards")
        if new_task.observed_at == old_task.observed_at and new_task != old_task:
            raise ValueError("PM task cannot change at the same observation")


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
    request_registry: RequestRegistrySnapshot | None = None
    pm_task_projection: PmTaskProjection | None = None
    runtime_binding: StatusRuntimeBinding | None = None

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
    active_runtime_bound: bool = False
    runtime_binding_verified: bool = False


class StatusUpsertRequest(BaseModel):
    """ingestが明示した行とcapacityだけを既存snapshotへ反映する。"""

    model_config = ConfigDict(extra="forbid")

    source: Literal["local-event-record"]
    items: list[WorkItem] = Field(default_factory=list, max_length=32)
    runtime_capacity: RuntimeCapacitySnapshot | None = None
    focus_summary: FocusSummary | None = None
    session_tree: SessionTreeSnapshot | None = None
    known_history: KnownHistorySnapshot | None = None
    runtime_binding: StatusRuntimeBinding | None = None

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
    runtime_binding: StatusRuntimeBinding | None = None
