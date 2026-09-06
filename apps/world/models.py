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


class ActionResult(ValueModel):
    event: Event
    world: WorldState
