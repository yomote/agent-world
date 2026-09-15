"""唯一の状態更新者。HTTPやActorを知らず、観測とAction受付だけを公開する。"""

from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from .models import (
    AcceptPlanAction,
    Action,
    ActionResult,
    DispatchShipmentAction,
    Entity,
    Event,
    EventHistory,
    LogisticsActionResult,
    LogisticsEvent,
    LogisticsPlan,
    LogisticsWorldState,
    PlanReference,
    Position,
    Road,
    StoreOrder,
    Truck,
    Warehouse,
    WorldState,
)


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


@dataclass(frozen=True)
class Principal:
    id: str
    kind: Literal["human", "agent", "service"]
    roles: frozenset[str]


LOCAL_PRINCIPALS = {
    "human-operator": Principal("human-operator", "human", frozenset({"scenario_operator"})),
    "allocator": Principal("allocator", "agent", frozenset({"inventory_allocator"})),
    "scheduler-w1": Principal("scheduler-w1", "agent", frozenset({"warehouse_scheduler"})),
    "dispatcher": Principal("dispatcher", "service", frozenset({"dispatcher"})),
}


def resolve_local_principal(principal_id: str) -> Principal | None:
    """ローカルprincipalをserver側registryから解決する。caller提供roleは受け取らない。"""
    return LOCAL_PRINCIPALS.get(principal_id)


def authorize(principal: Principal | None, action: str, resource: str) -> bool:
    """role・action・resourceを1か所で判定するローカル実験用policy。"""
    if principal is None or not resource.startswith("logistics:"):
        return False
    required = {
        "reset_scenario": ("human", "scenario_operator"),
        "accept_plan": ("human", "scenario_operator"),
        "dispatch_shipment": ("service", "dispatcher"),
    }.get(action)
    return bool(required and principal.kind == required[0] and required[1] in principal.roles)


class LogisticsSimulator:
    """物流Scenarioの唯一の状態更新者。Actorの計画計算やUIを知らない。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = self._new_state(truck_count=1)
        self._plans: dict[tuple[UUID, int], LogisticsPlan] = {}
        self._results: dict[UUID, LogisticsActionResult] = {}

    @staticmethod
    def _new_state(truck_count: int) -> LogisticsWorldState:
        return LogisticsWorldState(
            world_id=uuid4(),
            revision=0,
            horizon_ticks=2,
            warehouses=(
                Warehouse(id="W1", inventory=16, processing_capacity=4, processing_remaining=4),
                Warehouse(id="W2", inventory=16, processing_capacity=12, processing_remaining=12),
            ),
            orders=(
                StoreOrder(id="S1", requested=8, fulfilled=0, due_tick=2),
                StoreOrder(id="S2", requested=8, fulfilled=0, due_tick=2),
            ),
            trucks=tuple(
                Truck(id=f"T{index}", capacity=8, available_deliveries=1)
                for index in range(1, truck_count + 1)
            ),
            roads=(
                Road(warehouse_id="W1", store_id="S1", travel_ticks=1),
                Road(warehouse_id="W1", store_id="S2", travel_ticks=1),
                Road(warehouse_id="W2", store_id="S1", travel_ticks=2),
                Road(warehouse_id="W2", store_id="S2", travel_ticks=2),
            ),
        )

    def observe(self) -> LogisticsWorldState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def reset(self, principal_id: str, truck_count: int) -> LogisticsWorldState | None:
        with self._lock:
            principal = resolve_local_principal(principal_id)
            if not authorize(principal, "reset_scenario", f"logistics:{self._state.world_id}"):
                return None
            self._state = self._new_state(truck_count)
            self._plans = {}
            self._results = {}
            return self._state.model_copy(deep=True)

    def accept_plan(self, principal_id: str, action: AcceptPlanAction) -> LogisticsActionResult:
        with self._lock:
            cached = self._results.get(action.action_id)
            if cached is not None:
                return cached.model_copy(deep=True)
            principal = resolve_local_principal(principal_id)
            role = next(iter(principal.roles), "unknown") if principal else "unknown"
            reason: str | None = None
            status: Literal["success", "domain_failure", "authz_denied"] = "success"
            if not authorize(principal, "accept_plan", f"logistics:{self._state.world_id}:plan"):
                status, reason = "authz_denied", "authz_denied"
            elif action.expected_revision != self._state.revision:
                status, reason = "domain_failure", "stale_revision"
            elif (
                action.plan.input_world_id != self._state.world_id
                or action.plan.input_world_revision != self._state.revision
                or action.plan.run_id != action.run_id
                or action.plan.decision_id != action.decision_id
            ):
                status, reason = "domain_failure", "wrong_world"
            elif not self._valid_plan_shape(action.plan):
                status, reason = "domain_failure", "plan_mismatch"
            if status == "success":
                reason = "plan_accepted"
                reference = PlanReference(
                    plan_id=action.plan.plan_id,
                    version=action.plan.version,
                    run_id=action.run_id,
                    decision_id=action.decision_id,
                )
                self._plans[(action.plan.plan_id, action.plan.version)] = action.plan
                self._state = self._state.model_copy(
                    update={"revision": self._state.revision + 1, "accepted_plan": reference}
                )
            result = self._result(
                principal_id,
                principal.kind if principal else "unknown",
                role,
                action.action_id,
                action.run_id,
                action.decision_id,
                action.actor_id,
                "accept_logistics_plan",
                status,
                reason,
                action.plan.plan_id,
                action.plan.version,
            )
            self._results[action.action_id] = result
            return result.model_copy(deep=True)

    def dispatch(self, principal_id: str, action: DispatchShipmentAction) -> LogisticsActionResult:
        with self._lock:
            cached = self._results.get(action.action_id)
            if cached is not None:
                return cached.model_copy(deep=True)
            principal = resolve_local_principal(principal_id)
            role = next(iter(principal.roles), "unknown") if principal else "unknown"
            status: Literal["success", "domain_failure", "authz_denied"] = "success"
            reason: str = "shipment_dispatched"
            plan = self._plans.get((action.plan_id, action.plan_version))
            row = (
                next((item for item in plan.rows if item.row_id == action.row_id), None)
                if plan
                else None
            )
            truck = next((item for item in self._state.trucks if item.id == action.truck_id), None)
            warehouse = next(
                (item for item in self._state.warehouses if item.id == action.warehouse_id), None
            )
            order = next((item for item in self._state.orders if item.id == action.store_id), None)
            road = next(
                (
                    item
                    for item in self._state.roads
                    if item.warehouse_id == action.warehouse_id and item.store_id == action.store_id
                ),
                None,
            )
            accepted = self._state.accepted_plan
            is_accepted_row = bool(
                accepted
                and accepted.plan_id == action.plan_id
                and accepted.version == action.plan_version
                and row is not None
            )
            resource = (
                f"logistics:{self._state.world_id}:plan:{action.plan_id}:"
                f"v{action.plan_version}:row:{action.row_id}"
                if is_accepted_row
                else "unscoped"
            )
            if not authorize(principal, "dispatch_shipment", resource):
                status, reason = "authz_denied", "authz_denied"
            elif action.expected_revision != self._state.revision:
                status, reason = "domain_failure", "stale_revision"
            elif self._state.accepted_plan is None or plan is None or row is None:
                status, reason = "domain_failure", "plan_mismatch"
            elif (
                row.truck_id != action.truck_id
                or row.warehouse_id != action.warehouse_id
                or row.store_id != action.store_id
                or row.quantity != action.quantity
            ):
                status, reason = "domain_failure", "plan_mismatch"
            elif truck is None or truck.available_deliveries < 1:
                status, reason = "domain_failure", "truck_unavailable"
            elif action.quantity > truck.capacity:
                status, reason = "domain_failure", "truck_capacity_exceeded"
            elif warehouse is None or action.quantity > warehouse.processing_remaining:
                status, reason = "domain_failure", "warehouse_capacity_exceeded"
            elif action.quantity > warehouse.inventory:
                status, reason = "domain_failure", "insufficient_inventory"
            elif order is None or order.fulfilled >= order.requested:
                status, reason = "domain_failure", "order_already_fulfilled"
            elif action.quantity > order.requested - order.fulfilled:
                status, reason = "domain_failure", "order_quantity_exceeded"
            elif road is None:
                status, reason = "domain_failure", "route_unavailable"
            elif road.travel_ticks > min(order.due_tick, self._state.horizon_ticks):
                status, reason = "domain_failure", "arrival_after_horizon"
            if status == "success":
                assert truck is not None and warehouse is not None and order is not None
                self._state = self._state.model_copy(
                    update={
                        "revision": self._state.revision + 1,
                        "warehouses": tuple(
                            item.model_copy(
                                update={
                                    "inventory": item.inventory - action.quantity,
                                    "processing_remaining": item.processing_remaining
                                    - action.quantity,
                                }
                            )
                            if item.id == warehouse.id
                            else item
                            for item in self._state.warehouses
                        ),
                        "orders": tuple(
                            item.model_copy(update={"fulfilled": item.fulfilled + action.quantity})
                            if item.id == order.id
                            else item
                            for item in self._state.orders
                        ),
                        "trucks": tuple(
                            item.model_copy(
                                update={"available_deliveries": item.available_deliveries - 1}
                            )
                            if item.id == truck.id
                            else item
                            for item in self._state.trucks
                        ),
                    }
                )
            result = self._result(
                principal_id,
                principal.kind if principal else "unknown",
                role,
                action.action_id,
                action.run_id,
                action.decision_id,
                action.actor_id,
                "dispatch_shipment",
                status,
                reason,
                action.plan_id,
                action.plan_version,
                action.row_id,
                action.quantity if status == "success" else 0,
            )
            self._results[action.action_id] = result
            return result.model_copy(deep=True)

    def _valid_plan_shape(self, plan: LogisticsPlan) -> bool:
        row_ids = {row.row_id for row in plan.rows}
        truck_ids = {truck.id for truck in self._state.trucks}
        routes = {(road.warehouse_id, road.store_id) for road in self._state.roads}
        return (
            len(row_ids) == len(plan.rows)
            and all(row.truck_id in truck_ids for row in plan.rows)
            and all((row.warehouse_id, row.store_id) in routes for row in plan.rows)
        )

    def _result(
        self,
        principal_id: str,
        principal_kind: Literal["human", "agent", "service", "unknown"],
        role: str,
        action_id: UUID,
        run_id: UUID,
        decision_id: UUID,
        actor_id: str,
        action_type: Literal["accept_logistics_plan", "dispatch_shipment"],
        status: Literal["success", "domain_failure", "authz_denied"],
        reason: str,
        plan_id: UUID | None,
        plan_version: int | None,
        row_id: str | None = None,
        quantity: int = 0,
    ) -> LogisticsActionResult:
        return LogisticsActionResult(
            event=LogisticsEvent(
                event_id=uuid4(),
                action_id=action_id,
                run_id=run_id,
                decision_id=decision_id,
                actor_id=actor_id,
                action_type=action_type,
                status=status,
                reason=reason,
                principal_id=principal_id,
                principal_kind=principal_kind,
                principal_role=role,
                plan_id=plan_id,
                plan_version=plan_version,
                row_id=row_id,
                quantity=quantity,
                world_id=self._state.world_id,
                world_revision=self._state.revision,
            ),
            world=self._state.model_copy(deep=True),
        )
