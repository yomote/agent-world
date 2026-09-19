"""唯一の状態更新者。HTTPやActorを知らず、観測とAction受付だけを公開する。"""

import json
from collections import deque
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Lock
from typing import Literal
from uuid import uuid4

from .incident_models import (
    ApplyRequest,
    ApprovalRequest,
    EvidenceItem,
    ExecutionRequest,
    IncidentRunRequest,
    IncidentSnapshot,
    IncidentSummary,
    IncidentToolRequest,
    IncidentToolResult,
    ProposalResult,
    RecoveryChange,
    RecoveryProposal,
)
from .models import Action, ActionResult, Entity, Event, EventHistory, Position, WorldState


def resolve_move(
    position: Position, action: Action, width: int, height: int
) -> tuple[Position, Literal["moved", "invalid_move", "out_of_bounds"]]:
    """隣接1マス・境界内だけを許可する純粋な判定。"""
    if abs(action.dx) + abs(action.dy) != 1:
        return position, "invalid_move"
    target = Position(x=position.x + action.dx, y=position.y + action.dy)
    if not (0 <= target.x < width and 0 <= target.y < height):
        return position, "out_of_bounds"
    return target, "moved"


class WorldSimulator:
    def __init__(self, width: int = 8, height: int = 6) -> None:
        if width < 1 or height < 1:
            raise ValueError("Worldの幅と高さは1以上が必要です")
        self._lock = Lock()
        self._events: deque[Event] = deque(maxlen=80)
        self._state = WorldState(
            world_id=uuid4(),
            revision=0,
            width=width,
            height=height,
            entities=(Entity(id="A", position=Position(x=(width - 1) // 2, y=(height - 1) // 2)),),
        )

    def observe(self) -> WorldState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def observe_events(self) -> EventHistory:
        with self._lock:
            return EventHistory(world=self._state, events=tuple(self._events)).model_copy(deep=True)

    def apply(self, action: Action) -> ActionResult:
        with self._lock:
            entity = next(
                (item for item in self._state.entities if item.id == action.actor_id), None
            )
            before = entity.position if entity else None
            after = before
            reason: Literal["moved", "out_of_bounds", "unknown_actor", "invalid_move"]
            reason = "unknown_actor"
            if entity is not None:
                after, reason = resolve_move(
                    entity.position, action, self._state.width, self._state.height
                )
            success = reason == "moved"
            if success:
                assert entity is not None and after is not None
                self._state = self._state.model_copy(
                    update={
                        "revision": self._state.revision + 1,
                        "entities": tuple(
                            Entity(id=item.id, position=after) if item.id == entity.id else item
                            for item in self._state.entities
                        ),
                    }
                )
            event = Event(
                event_id=uuid4(),
                action=action,
                status="success" if success else "failure",
                reason=reason,
                before=before,
                after=after,
                world_id=self._state.world_id,
                world_revision=self._state.revision,
            )
            self._events.append(event)
            return ActionResult(
                event=event,
                world=self._state.model_copy(deep=True),
                events=tuple(self._events),
            )


_SCENARIOS = {
    1: {"cause": "partition", "partition": "east", "subscription": "west", "noise": "worker-ui"},
    2: {
        "cause": "rate",
        "partition": "east",
        "subscription": "east",
        "reset_minutes": 45,
        "noise": "dashboard",
    },
    3: {
        "cause": "schema",
        "partition": "east",
        "subscription": "east",
        "field": "delivery_window",
        "noise": "worker-v1.4",
    },
    4: {
        "cause": "partition",
        "partition": "north",
        "subscription": "south",
        "noise": "worker-v2.1",
    },
    5: {
        "cause": "rate",
        "partition": "west",
        "subscription": "west",
        "reset_minutes": 70,
        "noise": "queue-console",
    },
    6: {
        "cause": "schema",
        "partition": "south",
        "subscription": "south",
        "field": "route_code",
        "noise": "irrelevant-worker-v2.0",
    },
}


class IncidentSimulator:
    """隔離serviceを進める唯一の状態更新者。evaluator用真因は外部へ返さない。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[str, dict] = {}

    def reset(self, request: IncidentRunRequest) -> IncidentSnapshot:
        with self._lock:
            spec = deepcopy(_SCENARIOS[request.scenario_number])
            run_id = uuid4()
            now = datetime.now(UTC).replace(microsecond=0)
            orders = {
                f"O-{index:03d}": {
                    "id": f"O-{index:03d}",
                    "partition": spec["partition"],
                    "schema": "v2" if spec["cause"] == "schema" else "v1",
                    "state": "dead_letter" if spec["cause"] == "schema" else "pending",
                    "attempts": 1 if spec["cause"] in {"schema", "rate"} else 0,
                }
                for index in range(1, 13)
            }
            state = {
                "run_id": run_id,
                "scenario": request.scenario_number,
                "spec": spec,
                "now": now,
                "revision": 1,
                "orders": orders,
                "ledger": {},
                "subscription": spec["subscription"],
                "parser": "v1",
                "route": "primary",
                "quota": 0 if spec["cause"] == "rate" else 100,
                "events": [],
                "issued_evidence": {},
                "evidence_sequence": 0,
                "pending_proposal": None,
                "proposal_hash": None,
                "approved_hash": None,
                "approved_revision": None,
                "approval_consumed": False,
                "applied": [],
                "actions": {},
                "applied_action_id": None,
                "verification_consumed": False,
                "duplicate_rejections": 0,
                "status": "investigating",
            }
            self._runs[str(run_id)] = state
            self._event(state, "orders", "accepted", {"count": 12, "http_status": 200})
            self._event(state, "worker", "shipping_stalled", {"completed_last_10m": 0})
            if spec["cause"] == "schema":
                self._event(
                    state, "worker", "parse_error", {"field": spec["field"], "schema": "v2"}
                )
            elif spec["cause"] == "rate":
                self._event(state, "carrier", "request_failed", {"status": 429, "route": "primary"})
            else:
                self._event(state, "worker", "idle", {"subscription": spec["subscription"]})
            return self._snapshot(state)

    def observe(self, run_id: str) -> IncidentSnapshot:
        with self._lock:
            return self._snapshot(self._require(run_id))

    def tool(self, run_id: str, request: IncidentToolRequest) -> IncidentToolResult:
        with self._lock:
            state = self._require(run_id)
            evidence = self._tool_evidence(state, request)
            denied = any(item.data.get("status") == "denied" for item in evidence)
            return IncidentToolResult(
                tool=request.tool,
                status="denied" if denied else "success",
                summary=self._summary(state),
                evidence=evidence,
                error="verification_scope_mismatch" if denied else None,
            )

    def validate(self, run_id: str, proposal: RecoveryProposal) -> ProposalResult:
        with self._lock:
            state = self._require(run_id)
            digest = self._proposal_hash(proposal)
            if proposal.expected_revision != state["revision"]:
                return ProposalResult(
                    status="stale",
                    proposal_hash=digest,
                    summary=self._summary(state),
                    violations=("expected_revision_mismatch",),
                )
            violations = self._validate_changes(state, proposal.changes)
            violations.extend(self._validate_evidence(state, proposal.evidence_refs))
            if violations:
                return ProposalResult(
                    status="invalid",
                    proposal_hash=digest,
                    summary=self._summary(state),
                    violations=tuple(violations),
                )
            clone = deepcopy(state)
            self._apply_changes(clone, proposal.changes)
            self._tick(clone, 4, add_canaries=False)
            if len(clone["ledger"]) < 12:
                return ProposalResult(
                    status="invalid",
                    proposal_hash=digest,
                    summary=self._summary(state),
                    violations=("simulation_did_not_recover",),
                )
            return ProposalResult(
                status="valid",
                proposal_hash=digest,
                summary=self._summary(state),
                event_refs=(f"validation:{digest[:12]}",),
            )

    def request_execution(self, run_id: str, request: ExecutionRequest) -> ProposalResult:
        with self._lock:
            state = self._require(run_id)
            proposal = request.proposal
            digest = self._proposal_hash(proposal)
            action_id = str(request.action_id)
            fingerprint = json.dumps(request.model_dump(mode="json"), sort_keys=True)
            previous = state["actions"].get(action_id)
            if previous is not None:
                if previous["fingerprint"] == fingerprint:
                    return previous["result"]
                return ProposalResult(
                    status="failure",
                    proposal_hash=digest,
                    summary=self._summary(state),
                    violations=("action_id_conflict",),
                )
            if state["status"] != "investigating":
                return ProposalResult(
                    status="failure",
                    proposal_hash=digest,
                    summary=self._summary(state),
                    violations=("invalid_state_transition",),
                )
            if proposal.expected_revision != state["revision"]:
                return ProposalResult(
                    status="stale",
                    proposal_hash=digest,
                    summary=self._summary(state),
                    violations=("expected_revision_mismatch",),
                )
            violations = self._validate_changes(state, proposal.changes)
            violations.extend(self._validate_evidence(state, proposal.evidence_refs))
            if violations:
                return ProposalResult(
                    status="invalid",
                    proposal_hash=digest,
                    summary=self._summary(state),
                    violations=tuple(violations),
                )
            state["pending_proposal"] = proposal
            state["proposal_hash"] = digest
            state["approved_hash"] = None
            state["approved_revision"] = None
            state["approval_consumed"] = False
            state["verification_consumed"] = False
            state["status"] = "waiting_approval"
            state["revision"] += 1
            ref = self._event(state, "approval", "requested", {"proposal_hash": digest})
            result = ProposalResult(
                status="waiting_approval",
                proposal_hash=digest,
                summary=self._summary(state),
                event_refs=(ref,),
            )
            state["actions"][action_id] = {
                "fingerprint": fingerprint,
                "status": "pending",
                "result": result,
            }
            return result

    def approve(self, run_id: str, request: ApprovalRequest) -> ProposalResult:
        with self._lock:
            state = self._require(run_id)
            action_id = str(request.action_id)
            fingerprint = json.dumps(request.model_dump(mode="json"), sort_keys=True)
            previous = state["actions"].get(action_id)
            if previous is not None:
                if previous["fingerprint"] == fingerprint:
                    return previous["result"]
                return ProposalResult(
                    status="failure",
                    proposal_hash=request.proposal_hash,
                    summary=self._summary(state),
                    violations=("action_id_conflict",),
                )
            if state["status"] != "waiting_approval" or state["pending_proposal"] is None:
                return ProposalResult(
                    status="failure",
                    proposal_hash=request.proposal_hash,
                    summary=self._summary(state),
                    violations=("invalid_state_transition",),
                )
            if request.expected_revision != state["revision"]:
                return ProposalResult(
                    status="stale",
                    proposal_hash=request.proposal_hash,
                    summary=self._summary(state),
                    violations=("expected_revision_mismatch",),
                )
            if request.proposal_hash != state["proposal_hash"]:
                return ProposalResult(
                    status="failure",
                    proposal_hash=request.proposal_hash,
                    summary=self._summary(state),
                    violations=("approval_hash_mismatch",),
                )
            if not request.approve:
                state["status"] = "stopped"
                state["revision"] += 1
                ref = self._event(state, "approval", "rejected", {"approver": "local-human"})
                state["pending_proposal"] = None
                state["approved_hash"] = None
                result = ProposalResult(
                    status="failure",
                    proposal_hash=request.proposal_hash,
                    summary=self._summary(state),
                    event_refs=(ref,),
                )
                state["actions"][action_id] = {
                    "fingerprint": fingerprint,
                    "status": "rejected",
                    "result": result,
                }
                return result
            state["approved_hash"] = request.proposal_hash
            state["revision"] += 1
            state["approved_revision"] = state["revision"]
            state["approval_consumed"] = False
            state["status"] = "applying"
            ref = self._event(
                state,
                "approval",
                "approved",
                {"approver": "local-human", "proposal_hash": request.proposal_hash},
            )
            result = ProposalResult(
                status="approved",
                proposal_hash=request.proposal_hash,
                summary=self._summary(state),
                event_refs=(ref,),
            )
            state["actions"][action_id] = {
                "fingerprint": fingerprint,
                "status": "approved",
                "result": result,
            }
            return result

    def apply_approved(self, run_id: str, request: ApplyRequest) -> ProposalResult:
        with self._lock:
            state = self._require(run_id)
            action_id = str(request.action_id)
            fingerprint = json.dumps(request.model_dump(mode="json"), sort_keys=True)
            previous = state["actions"].get(action_id)
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    return ProposalResult(
                        status="failure",
                        proposal_hash=request.proposal_hash,
                        summary=self._summary(state),
                        violations=("action_id_conflict",),
                    )
                return previous["result"]
            if state["status"] != "applying":
                return ProposalResult(
                    status="failure",
                    proposal_hash=request.proposal_hash,
                    summary=self._summary(state),
                    violations=("invalid_state_transition",),
                )
            if request.expected_revision != state["revision"]:
                result = ProposalResult(
                    status="stale",
                    proposal_hash=request.proposal_hash,
                    summary=self._summary(state),
                    violations=("expected_revision_mismatch",),
                )
                state["actions"][action_id] = {
                    "fingerprint": fingerprint,
                    "status": "failed",
                    "result": result,
                }
                return result
            if (
                request.proposal_hash != state["approved_hash"]
                or state["pending_proposal"] is None
                or state["approved_revision"] != request.expected_revision
                or state["approval_consumed"]
            ):
                result = ProposalResult(
                    status="failure",
                    proposal_hash=request.proposal_hash,
                    summary=self._summary(state),
                    violations=("not_approved",),
                )
                state["actions"][action_id] = {
                    "fingerprint": fingerprint,
                    "status": "failed",
                    "result": result,
                }
                return result
            proposal = state["pending_proposal"]
            self._apply_changes(state, proposal.changes)
            state["status"] = "verifying"
            state["revision"] += 1
            state["applied_action_id"] = action_id
            state["approval_consumed"] = True
            ref = self._event(
                state,
                "world",
                "proposal_applied",
                {"proposal_hash": request.proposal_hash, "action_id": action_id},
            )
            result = ProposalResult(
                status="applied",
                proposal_hash=request.proposal_hash,
                summary=self._summary(state),
                event_refs=(ref,),
            )
            state["actions"][action_id] = {
                "fingerprint": fingerprint,
                "status": "applied",
                "result": result,
            }
            return result

    def _tool_evidence(self, state: dict, request: IncidentToolRequest) -> tuple[EvidenceItem, ...]:
        args = request.args
        if request.tool == "observe_system":
            data = {
                "services": {
                    "orders": "healthy",
                    "queue": "degraded",
                    "worker": "healthy",
                    "carrier": "healthy" if state["quota"] else "limited",
                },
                **self._summary(state).model_dump(mode="json"),
            }
            return (self._evidence(state, "metrics", data),)
        if request.tool == "query_events":
            service = str(args.get("service", "worker"))
            return tuple(item for item in state["events"] if item.source == service)[-10:]
        if request.tool == "inspect_queue":
            partition = str(args.get("partition", state["spec"]["partition"]))
            limit = max(1, min(int(args.get("limit", 5)), 20))
            selected = [
                o
                for o in state["orders"].values()
                if o["partition"] == partition and o["state"] != "completed"
            ][:limit]
            counts = {
                kind: sum(o["state"] == kind for o in state["orders"].values())
                for kind in ("pending", "dead_letter", "completed")
            }
            return (
                self._evidence(
                    state, "queue", {"partition": partition, "counts": counts, "sample": selected}
                ),
            )
        if request.tool == "read_service_config":
            service = str(args.get("service", "worker"))
            configs = {
                "worker": {
                    "subscription": state["subscription"],
                    "parser": state["parser"],
                    "deployment": f"worker-{state['parser']}",
                },
                "carrier": {"route": state["route"]},
            }
            return (
                self._evidence(
                    state, "config", {"service": service, "config": configs.get(service, {})}
                ),
            )
        if request.tool == "list_changes":
            return (
                self._evidence(
                    state,
                    "changes",
                    {
                        "changes": [
                            {
                                "service": "worker",
                                "at": (state["now"] - timedelta(minutes=14)).isoformat(),
                                "diff": {"deployment": state["spec"]["noise"]},
                            }
                        ]
                    },
                ),
            )
        if request.tool == "search_knowledge":
            query = str(args.get("query", ""))
            docs = [
                {
                    "ref": "kb:queue:v3",
                    "scope": "queue",
                    "text": "Queue partition must match the worker subscription.",
                },
                {
                    "ref": "kb:carrier:v2",
                    "scope": "carrier",
                    "text": (
                        "Spare route is supported but tracking may be delayed up to 30 minutes."
                    ),
                },
                {
                    "ref": "kb:schema:v4",
                    "scope": "worker",
                    "text": (
                        "Payload v2 requires parser v2; incompatible messages may be requeued "
                        f"after deployment. Query={query[:80]}"
                    ),
                },
            ]
            return tuple(self._evidence(state, "knowledge", doc) for doc in docs)
        if request.tool == "probe_dependency":
            return (
                self._evidence(
                    state,
                    "carrier",
                    {
                        "route": state["route"],
                        "status": 429
                        if state["quota"] == 0 and state["route"] == "primary"
                        else 200,
                        "quota": state["quota"],
                        "reset_at": (
                            state["now"] + timedelta(minutes=state["spec"].get("reset_minutes", 0))
                        ).isoformat(),
                        "spare_available": True,
                        "tracking_delay_minutes": 30,
                    },
                ),
            )
        if request.tool == "advance_and_verify":
            verification_action_id = str(args.get("verification_action_id", ""))
            fingerprint = json.dumps(
                {"operation": "advance_and_verify", "args": args}, sort_keys=True
            )
            previous = state["actions"].get(verification_action_id)
            if previous is not None:
                if previous["fingerprint"] == fingerprint:
                    return previous["result"]
                return (
                    self._evidence(
                        state,
                        "verification",
                        {"status": "denied", "reason": "action_id_conflict"},
                    ),
                )
            proposal = state["pending_proposal"]
            if (
                state["status"] != "verifying"
                or state["verification_consumed"]
                or proposal is None
                or args.get("proposal_hash") != state["approved_hash"]
                or args.get("action_id") != state["applied_action_id"]
                or args.get("ticks") != proposal.verification_ticks
            ):
                return (
                    self._evidence(
                        state,
                        "verification",
                        {"status": "denied", "reason": "verification_scope_mismatch"},
                    ),
                )
            ticks = proposal.verification_ticks
            self._tick(state, ticks, add_canaries=True)
            stats = self._ledger_stats(state)
            evidence = (
                self._evidence(
                    state,
                    "verification",
                    {
                        "ticks": ticks,
                        "target_orders": 12 + proposal.canary_count,
                        "terminal_orders": stats["unique_order_ids"],
                        "unique_instruction_ids": stats["unique_instruction_ids"],
                        "unique_idempotency_keys": stats["unique_idempotency_keys"],
                        "duplicate_rejections": stats["duplicate_rejections"],
                        "canaries_completed": sum(
                            key.startswith("ship:C-") for key in state["ledger"]
                        ),
                        "observation_window": "bounded_ticks",
                    },
                ),
            )
            state["verification_consumed"] = True
            state["actions"][verification_action_id] = {
                "fingerprint": fingerprint,
                "status": "verified",
                "result": evidence,
            }
            return evidence
        if request.tool == "lookup_action_status":
            action_id = str(args.get("action_id", ""))
            record = state["actions"].get(action_id)
            return (
                self._evidence(
                    state,
                    "actions",
                    {"action_id": action_id, "status": record["status"] if record else "unknown"},
                ),
            )
        return ()

    def _validate_changes(self, state: dict, changes: tuple[RecoveryChange, ...]) -> list[str]:
        errors: list[str] = []
        for change in changes:
            if change.operation == "set_config" and not (
                (
                    change.service == "worker"
                    and change.key == "subscription"
                    and change.value in {"east", "west", "north", "south"}
                )
                or (
                    change.service == "carrier"
                    and change.key == "route"
                    and change.value in {"primary", "spare"}
                )
            ):
                errors.append("set_config_not_allowed")
            elif change.operation == "select_deployment" and not (
                change.service == "worker" and change.value in {"v1", "v2"}
            ):
                errors.append("deployment_not_allowed")
            elif change.operation == "requeue":
                if (
                    change.service != "queue"
                    or change.expected_state != "dead_letter"
                    or not change.order_ids
                ):
                    errors.append("requeue_not_allowed")
                elif any(
                    state["orders"].get(order_id, {}).get("state") != "dead_letter"
                    for order_id in change.order_ids
                ):
                    errors.append("order_state_mismatch")
        return errors

    @staticmethod
    def _validate_evidence(state: dict, refs: tuple[str, ...]) -> list[str]:
        if any(ref not in state["issued_evidence"] for ref in refs):
            return ["evidence_not_issued_for_run"]
        sources = {state["issued_evidence"][ref] for ref in refs}
        if len(refs) < 2 or len(sources) < 2:
            return ["insufficient_corroborating_evidence"]
        return []

    def _apply_changes(self, state: dict, changes: tuple[RecoveryChange, ...]) -> None:
        for change in changes:
            if change.operation == "set_config" and change.key == "subscription":
                state["subscription"] = change.value
            elif change.operation == "set_config" and change.key == "route":
                state["route"] = change.value
            elif change.operation == "select_deployment":
                state["parser"] = change.value
            elif change.operation == "requeue":
                for order_id in change.order_ids:
                    state["orders"][order_id]["state"] = "pending"
            state["applied"].append(change)

    def _tick(self, state: dict, ticks: int, *, add_canaries: bool) -> None:
        if add_canaries and not any(key.startswith("C-") for key in state["orders"]):
            for index in range(1, 3):
                suffix = str(state["run_id"]).split("-")[0]
                canary_id = f"C-{suffix}-{index:03d}"
                state["orders"][canary_id] = {
                    "id": canary_id,
                    "partition": state["spec"]["partition"],
                    "schema": "v2" if state["parser"] == "v2" else "v1",
                    "state": "pending",
                    "attempts": 0,
                }
        for _ in range(ticks):
            for order in state["orders"].values():
                if order["state"] != "pending" or order["partition"] != state["subscription"]:
                    continue
                if order["schema"] == "v2" and state["parser"] != "v2":
                    order["state"] = "dead_letter"
                    continue
                if state["route"] == "primary" and state["quota"] == 0:
                    order["attempts"] += 1
                    continue
                key = f"ship:{order['id']}"
                if key not in state["ledger"]:
                    state["ledger"][key] = {
                        "order_id": order["id"],
                        "instruction_id": f"SI-{order['id'][2:]}",
                        "idempotency_key": key,
                    }
                else:
                    state["duplicate_rejections"] += 1
                order["state"] = "completed"
            state["now"] += timedelta(minutes=1)
        state["revision"] += 1
        if all(order["state"] == "completed" for order in state["orders"].values()):
            state["status"] = "recovered"
        self._event(
            state,
            "ledger",
            "verification_window",
            {"completed": len(state["ledger"]), **self._ledger_stats(state)},
        )

    @staticmethod
    def _ledger_stats(state: dict) -> dict[str, int]:
        rows = tuple(state["ledger"].values())
        return {
            "unique_order_ids": len({row["order_id"] for row in rows}),
            "unique_instruction_ids": len({row["instruction_id"] for row in rows}),
            "unique_idempotency_keys": len({row["idempotency_key"] for row in rows}),
            "duplicate_rejections": state["duplicate_rejections"],
        }

    def _summary(self, state: dict) -> IncidentSummary:
        completed = sum(order["state"] == "completed" for order in state["orders"].values())
        return IncidentSummary(
            run_id=state["run_id"],
            revision=state["revision"],
            accepted=len(state["orders"]),
            incomplete=len(state["orders"]) - completed,
            completed=completed,
            duplicate_count=state["duplicate_rejections"],
            state=state["status"],
        )

    def _snapshot(self, state: dict) -> IncidentSnapshot:
        pending = sum(order["state"] == "pending" for order in state["orders"].values())
        dead = sum(order["state"] == "dead_letter" for order in state["orders"].values())
        return IncidentSnapshot(
            scenario_number=state["scenario"],
            summary=self._summary(state),
            services={
                "orders": {"health": "healthy"},
                "queue": {"health": "degraded", "pending": pending, "dead_letter": dead},
                "worker": {"health": "healthy"},
                "carrier": {"health": "limited" if state["quota"] == 0 else "healthy"},
            },
            evidence=tuple(state["events"]),
            pending_proposal=state["pending_proposal"],
            proposal_hash=state["proposal_hash"],
            approved_hash=state["approved_hash"],
            applied_changes=tuple(state["applied"]),
        )

    def _event(self, state: dict, source: str, kind: str, data: dict) -> str:
        ref = f"evt:{len(state['events']) + 1:04d}"
        state["events"].append(
            EvidenceItem(
                ref=ref,
                source=source,
                timestamp=state["now"].isoformat(),
                data={"kind": kind, **data},
            )
        )
        state["issued_evidence"][ref] = source
        return ref

    def _evidence(self, state: dict, source: str, data: dict) -> EvidenceItem:
        state["evidence_sequence"] += 1
        ref = f"obs:{state['revision']}:{source}:{state['evidence_sequence']}"
        state["issued_evidence"][ref] = source
        return EvidenceItem(
            ref=ref,
            source=source,
            timestamp=state["now"].isoformat(),
            data=data,
        )

    def _require(self, run_id: str) -> dict:
        if run_id not in self._runs:
            raise KeyError(run_id)
        return self._runs[run_id]

    @staticmethod
    def _proposal_hash(proposal: RecoveryProposal) -> str:
        payload = json.dumps(
            proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return sha256(payload.encode()).hexdigest()
