"""唯一の状態更新者。HTTPやActorを知らず、観測とAction受付だけを公開する。"""

from collections import deque
from threading import Lock
from typing import Literal
from uuid import uuid4

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
