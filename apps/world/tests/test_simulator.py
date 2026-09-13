from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError
from world.models import Action
from world.simulator import WorldSimulator


def move(dx=1, dy=0, actor_id="A") -> Action:
    return Action(action_id=uuid4(), actor_id=actor_id, type="move", dx=dx, dy=dy)


def test_move_commits_position_and_matching_event():
    """成功のEventと観測Stateが食い違っても静かに通る回帰を防ぐ。"""
    simulator = WorldSimulator()
    before = simulator.observe()
    action = move()
    result = simulator.apply(action)
    assert result.event.action == action
    assert result.event.status == "success"
    assert result.event.before == before.entities[0].position
    assert result.event.after.x == before.entities[0].position.x + 1
    assert result.event.after.y == before.entities[0].position.y
    assert result.world.entities[0].position == result.event.after
    assert result.event.world_revision == result.world.revision == 1
    assert result.event.world_id == before.world_id == result.world.world_id
    assert simulator.observe() == result.world
    assert before.revision == 0


@pytest.mark.parametrize("dx,dy", [(0, 0), (1, 1), (2, 0), (-2, 0)])
def test_invalid_move_does_not_mutate(dx, dy):
    """斜め移動・静止・瞬間移動がWorldに紛れ込む回帰を防ぐ。"""
    simulator = WorldSimulator()
    before = simulator.observe()
    result = simulator.apply(move(dx, dy))
    assert result.event.status == "failure"
    assert result.event.reason == "invalid_move"
    assert result.event.before == result.event.after
    # failureのEventが返却snapshotとは別のWorldやrevisionを指す回帰を防ぐ。
    assert result.event.world_id == result.world.world_id
    assert result.event.world_revision == result.world.revision == before.revision
    assert simulator.observe() == result.world == before


@pytest.mark.parametrize("dx,dy", [(1, 0), (-1, 0), (0, 1), (0, -1)])
def test_each_boundary_rejects_without_mutation(dx, dy):
    """どの辺からも範囲外に出ず、failureがStateを変えないことを守る。"""
    simulator = WorldSimulator(width=3, height=3)
    assert simulator.apply(move(dx, dy)).event.status == "success"
    before = simulator.observe()
    result = simulator.apply(move(dx, dy))
    assert result.event.status == "failure"
    assert result.event.reason == "out_of_bounds"
    assert result.event.before == result.event.after
    assert simulator.observe() == result.world == before


def test_unknown_actor_cannot_move_a():
    """存在しないActorの指示がAへ誤適用される回帰を防ぐ。"""
    simulator = WorldSimulator()
    before = simulator.observe()
    result = simulator.apply(move(actor_id="missing"))
    assert result.event.reason == "unknown_actor"
    assert result.event.status == "failure"
    assert result.event.before is result.event.after is None
    assert simulator.observe() == before


def test_observation_cannot_write_back_to_world():
    """観測値の共有参照を通じてActorが内部Stateを変更する抜け道を防ぐ。"""
    simulator = WorldSimulator()
    observed = simulator.observe()
    with pytest.raises(ValidationError):
        observed.entities[0].position.x = 999
    with pytest.raises(ValidationError):
        observed.revision = 999
    payload = observed.model_dump()
    payload["entities"][0]["position"]["x"] = 999
    assert simulator.observe() == observed


def test_concurrent_actions_are_serialized():
    """同時Actionの更新消失と重複revisionを防ぐ。"""
    simulator = WorldSimulator(width=100, height=1)
    start_x = simulator.observe().entities[0].position.x
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(simulator.apply, [move() for _ in range(20)]))
    assert all(result.event.status == "success" for result in results)
    assert sorted(result.world.revision for result in results) == list(range(1, 21))
    assert simulator.observe().entities[0].position.x == start_x + 20
    assert all(result.world.entities[0].position == result.event.after for result in results)
    assert all(result.events[-1] == result.event for result in results)
    assert all(len(result.events) == result.world.revision for result in results)


def test_event_history_is_bounded_ordered_and_volatile():
    """failureでrevisionが増えなくても履歴を残し、81件目で最古を破棄する。"""
    simulator = WorldSimulator(width=1, height=1)
    empty = simulator.observe_events()
    assert empty.events == ()
    events = [simulator.apply(move()).event for _ in range(81)]
    observed = simulator.observe_events()
    assert observed.events == tuple(events[-80:])
    assert observed.world == empty.world
    assert all(event.status == "failure" for event in observed.events)
    assert len({event.event_id for event in observed.events}) == 80
    assert empty.events == ()
    restarted = WorldSimulator().observe_events()
    assert restarted.events == ()
    assert restarted.world.world_id != observed.world.world_id


def test_event_history_snapshot_cannot_modify_simulator():
    """取得した履歴やPOST応答の参照からSimulator所有の履歴を変更する抜け道を防ぐ。"""
    simulator = WorldSimulator()
    result = simulator.apply(move())
    observed = simulator.observe_events()
    with pytest.raises(ValidationError):
        observed.events[0].action.dx = 100
    with pytest.raises(ValidationError):
        result.events[0].after.x = 100
    payload = observed.model_dump()
    payload["events"][0]["action"]["dx"] = 100
    simulator.apply(move())
    assert len(observed.events) == len(result.events) == 1
    assert simulator.observe_events().events[0] == result.event


def test_concurrent_history_reads_match_world_snapshot():
    """World読取と履歴読取の間にActionが入り、異なる時点を返す回帰を防ぐ。"""
    simulator = WorldSimulator(width=200, height=1)

    def apply_and_observe(_):
        simulator.apply(move())
        return simulator.observe_events()

    with ThreadPoolExecutor(max_workers=8) as executor:
        snapshots = list(executor.map(apply_and_observe, range(40)))
    for snapshot in snapshots:
        assert len(snapshot.events) == snapshot.world.revision
        assert snapshot.events[-1].world_revision == snapshot.world.revision
        assert snapshot.events[-1].after == snapshot.world.entities[0].position
        assert all(event.world_id == snapshot.world.world_id for event in snapshot.events)
