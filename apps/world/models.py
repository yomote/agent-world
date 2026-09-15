"""通信契約の正典。ネストもimmutableな値で観測と所有権を分ける。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class ValueModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Position(ValueModel):
    x: StrictInt
    y: StrictInt


class Entity(ValueModel):
    id: str
    position: Position


class WorldState(ValueModel):
    world_id: UUID
    revision: int
    width: int
    height: int
    entities: tuple[Entity, ...]


class Action(ValueModel):
    action_id: UUID
    actor_id: Annotated[str, Field(min_length=1, max_length=64)]
    type: Literal["move"]
    dx: StrictInt
    dy: StrictInt


class Event(ValueModel):
    event_id: UUID
    action: Action
    status: Literal["success", "failure"]
    reason: Literal["moved", "out_of_bounds", "unknown_actor", "invalid_move"]
    before: Position | None
    after: Position | None
    world_id: UUID
    world_revision: int


class EventHistory(ValueModel):
    world: WorldState
    events: Annotated[tuple[Event, ...], Field(max_length=80)]


class ActionResult(EventHistory):
    event: Event


class Warehouse(ValueModel):
    id: Literal["W1", "W2"]
    inventory: int
    processing_capacity: int
    processing_remaining: int


class StoreOrder(ValueModel):
    id: Literal["S1", "S2"]
    requested: int
    fulfilled: int
    due_tick: int


class Truck(ValueModel):
    id: str
    capacity: int
    available_deliveries: int


class Road(ValueModel):
    warehouse_id: Literal["W1", "W2"]
    store_id: Literal["S1", "S2"]
    travel_ticks: int


class PlanReference(ValueModel):
    plan_id: UUID
    version: int
    run_id: UUID
    decision_id: UUID


class LogisticsWorldState(ValueModel):
    world_id: UUID
    revision: int
    horizon_ticks: int
    warehouses: tuple[Warehouse, ...]
    orders: tuple[StoreOrder, ...]
    trucks: tuple[Truck, ...]
    roads: tuple[Road, ...]
    accepted_plan: PlanReference | None = None


class ShipmentPlanRow(ValueModel):
    row_id: str
    warehouse_id: Literal["W1", "W2"]
    store_id: Literal["S1", "S2"]
    quantity: Annotated[StrictInt, Field(ge=1)]
    truck_id: str


class RoleHandoff(ValueModel):
    from_role: Literal[
        "inventory_allocator", "warehouse_scheduler", "dispatcher", "due_date_coordinator"
    ]
    to_role: Literal[
        "warehouse_scheduler", "dispatcher", "due_date_coordinator", "scenario_operator"
    ]
    artifact: str


class LogisticsPlan(ValueModel):
    plan_id: UUID
    version: int
    publication_state: Literal["proposed"]
    run_id: UUID
    decision_id: UUID
    input_world_id: UUID
    input_world_revision: int
    created_by: Literal["nearest-warehouse-v1", "role-team-v1"]
    rows: tuple[ShipmentPlanRow, ...]
    handoffs: tuple[RoleHandoff, ...]
    reason: str


class AcceptPlanAction(ValueModel):
    action_id: UUID
    run_id: UUID
    decision_id: UUID
    actor_id: Annotated[str, Field(min_length=1, max_length=64)]
    type: Literal["accept_logistics_plan"]
    expected_revision: int
    plan: LogisticsPlan


class DispatchShipmentAction(ValueModel):
    action_id: UUID
    run_id: UUID
    decision_id: UUID
    actor_id: Annotated[str, Field(min_length=1, max_length=64)]
    type: Literal["dispatch_shipment"]
    expected_revision: int
    plan_id: UUID
    plan_version: int
    row_id: str
    truck_id: str
    warehouse_id: Literal["W1", "W2"]
    store_id: Literal["S1", "S2"]
    quantity: Annotated[StrictInt, Field(ge=1)]


class LogisticsEvent(ValueModel):
    event_id: UUID
    action_id: UUID
    run_id: UUID
    decision_id: UUID
    actor_id: str
    action_type: Literal["accept_logistics_plan", "dispatch_shipment"]
    status: Literal["success", "domain_failure", "authz_denied"]
    reason: Literal[
        "plan_accepted",
        "shipment_dispatched",
        "authz_denied",
        "stale_revision",
        "wrong_world",
        "plan_mismatch",
        "truck_unavailable",
        "truck_capacity_exceeded",
        "warehouse_capacity_exceeded",
        "insufficient_inventory",
        "order_already_fulfilled",
        "order_quantity_exceeded",
        "route_unavailable",
        "arrival_after_horizon",
    ]
    principal_id: str
    principal_kind: Literal["human", "agent", "service", "unknown"]
    principal_role: str
    plan_id: UUID | None
    plan_version: int | None
    row_id: str | None
    quantity: int
    world_id: UUID
    world_revision: int


class LogisticsActionResult(ValueModel):
    event: LogisticsEvent
    world: LogisticsWorldState


class ResetLogisticsScenario(ValueModel):
    truck_count: Annotated[StrictInt, Field(ge=1, le=3)]
