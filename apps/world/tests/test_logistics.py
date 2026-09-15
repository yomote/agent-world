from uuid import uuid4

import pytest
from pydantic import ValidationError
from world.models import (
    AcceptPlanAction,
    DispatchShipmentAction,
    LogisticsPlan,
    RoleHandoff,
    ShipmentPlanRow,
)
from world.simulator import LogisticsSimulator


def make_plan(simulator: LogisticsSimulator, team: bool = False) -> LogisticsPlan:
    world = simulator.observe()
    run_id = uuid4()
    decision_id = uuid4()
    rows = (
        ShipmentPlanRow(
            row_id="shipment-1",
            warehouse_id="W1",
            store_id="S1",
            quantity=4,
            truck_id="T1",
        ),
        ShipmentPlanRow(
            row_id="shipment-2",
            warehouse_id="W2" if team else "W1",
            store_id="S1" if team else "S2",
            quantity=4,
            truck_id="T2",
        ),
        *(
            (
                ShipmentPlanRow(
                    row_id="shipment-3",
                    warehouse_id="W2",
                    store_id="S2",
                    quantity=8,
                    truck_id="T3",
                ),
            )
            if team
            else ()
        ),
    )
    return LogisticsPlan(
        plan_id=uuid4(),
        version=2 if team else 1,
        publication_state="proposed",
        run_id=run_id,
        decision_id=decision_id,
        input_world_id=world.world_id,
        input_world_revision=world.revision,
        created_by="role-team-v1" if team else "nearest-warehouse-v1",
        rows=rows,
        handoffs=(
            RoleHandoff(
                from_role="inventory_allocator",
                to_role="warehouse_scheduler",
                artifact="在庫配分案",
            ),
            RoleHandoff(
                from_role="warehouse_scheduler",
                to_role="dispatcher",
                artifact="出荷可能量",
            ),
            RoleHandoff(
                from_role="dispatcher",
                to_role="due_date_coordinator",
                artifact="配車計画",
            ),
            RoleHandoff(
                from_role="due_date_coordinator",
                to_role="scenario_operator",
                artifact="納期確認済み計画",
            ),
        ),
        reason="固定Scenarioを観測して作った決定論的計画",
    )


def accept(simulator: LogisticsSimulator, plan: LogisticsPlan, principal="human-operator"):
    return simulator.accept_plan(
        principal,
        AcceptPlanAction(
            action_id=uuid4(),
            run_id=plan.run_id,
            decision_id=plan.decision_id,
            actor_id="scenario-operator",
            type="accept_logistics_plan",
            expected_revision=simulator.observe().revision,
            plan=plan,
        ),
    )


def dispatch(simulator: LogisticsSimulator, plan: LogisticsPlan, index: int, action_id=None):
    row = plan.rows[index]
    return simulator.dispatch(
        "dispatcher",
        DispatchShipmentAction(
            action_id=action_id or uuid4(),
            run_id=plan.run_id,
            decision_id=plan.decision_id,
            actor_id="logistics-dispatcher",
            type="dispatch_shipment",
            expected_revision=simulator.observe().revision,
            plan_id=plan.plan_id,
            plan_version=plan.version,
            row_id=row.row_id,
            truck_id=row.truck_id,
            warehouse_id=row.warehouse_id,
            store_id=row.store_id,
            quantity=row.quantity,
        ),
    )


def test_same_initial_conditions_show_capacity_bottleneck_and_team_improvement():
    """比較runへ消費済み状態が漏れ、4/16対16/16が偽装される回帰を防ぐ。"""
    simulator = LogisticsSimulator()

    assert simulator.reset("human-operator", 3) is not None
    baseline = make_plan(simulator)
    assert accept(simulator, baseline).event.status == "success"
    assert dispatch(simulator, baseline, 0).event.status == "success"
    failure = dispatch(simulator, baseline, 1)
    assert failure.event.status == "domain_failure"
    assert failure.event.reason == "warehouse_capacity_exceeded"
    assert sum(order.fulfilled for order in simulator.observe().orders) == 4

    reset_world = simulator.reset("human-operator", 3)
    assert reset_world is not None
    assert all(order.fulfilled == 0 for order in reset_world.orders)
    team = make_plan(simulator, team=True)
    assert accept(simulator, team).event.status == "success"
    assert all(dispatch(simulator, team, index).event.status == "success" for index in range(3))
    assert sum(order.fulfilled for order in simulator.observe().orders) == 16


def test_denied_action_cannot_change_operational_state_or_accepted_artifact():
    """Actor自己申告や不正principalで在庫・能力・配送・plan採否が変わる回帰を防ぐ。"""
    simulator = LogisticsSimulator()
    assert simulator.reset("human-operator", 3) is not None
    plan = make_plan(simulator, team=True)
    before = simulator.observe()
    denied_accept = accept(simulator, plan, principal="dispatcher")
    assert denied_accept.event.status == "authz_denied"
    assert denied_accept.event.principal_kind == "service"
    assert simulator.observe() == before

    assert accept(simulator, plan).event.status == "success"
    accepted = simulator.observe()
    row = plan.rows[0]
    denied_dispatch = simulator.dispatch(
        "allocator",
        DispatchShipmentAction(
            action_id=uuid4(),
            run_id=plan.run_id,
            decision_id=plan.decision_id,
            actor_id="logistics-dispatcher",
            type="dispatch_shipment",
            expected_revision=accepted.revision,
            plan_id=plan.plan_id,
            plan_version=plan.version,
            row_id=row.row_id,
            truck_id=row.truck_id,
            warehouse_id=row.warehouse_id,
            store_id=row.store_id,
            quantity=row.quantity,
        ),
    )
    assert denied_dispatch.event.status == "authz_denied"
    assert denied_dispatch.event.principal_kind == "agent"
    assert simulator.observe() == accepted


def test_duplicate_action_returns_original_event_without_double_consumption():
    """結果不明後の照会相当の再要求で在庫と注文を二重更新する回帰を防ぐ。"""
    simulator = LogisticsSimulator()
    assert simulator.reset("human-operator", 3) is not None
    plan = make_plan(simulator, team=True)
    accept(simulator, plan)
    action_id = uuid4()
    first = dispatch(simulator, plan, 0, action_id)
    state_after_first = simulator.observe()
    duplicate = dispatch(simulator, plan, 0, action_id)
    assert duplicate == first
    assert simulator.observe() == state_after_first


def test_stale_revision_is_domain_failure_without_mutation():
    """古い観測のActionを確定し、並行更新を上書きする回帰を防ぐ。"""
    simulator = LogisticsSimulator()
    assert simulator.reset("human-operator", 3) is not None
    plan = make_plan(simulator, team=True)
    accept(simulator, plan)
    row = plan.rows[0]
    before = simulator.observe()
    result = simulator.dispatch(
        "dispatcher",
        DispatchShipmentAction(
            action_id=uuid4(),
            run_id=plan.run_id,
            decision_id=plan.decision_id,
            actor_id="logistics-dispatcher",
            type="dispatch_shipment",
            expected_revision=before.revision - 1,
            plan_id=plan.plan_id,
            plan_version=plan.version,
            row_id=row.row_id,
            truck_id=row.truck_id,
            warehouse_id=row.warehouse_id,
            store_id=row.store_id,
            quantity=row.quantity,
        ),
    )
    assert result.event.status == "domain_failure"
    assert result.event.reason == "stale_revision"
    assert simulator.observe() == before


def test_world_rejects_dispatch_that_does_not_match_accepted_plan():
    """Actor提案だけでSimulator検証を迂回し、別数量を確定する回帰を防ぐ。"""
    simulator = LogisticsSimulator()
    assert simulator.reset("human-operator", 3) is not None
    plan = make_plan(simulator, team=True)
    accept(simulator, plan)
    row = plan.rows[0]
    before = simulator.observe()
    result = simulator.dispatch(
        "dispatcher",
        DispatchShipmentAction(
            action_id=uuid4(),
            run_id=plan.run_id,
            decision_id=plan.decision_id,
            actor_id="logistics-dispatcher",
            type="dispatch_shipment",
            expected_revision=before.revision,
            plan_id=plan.plan_id,
            plan_version=plan.version,
            row_id=row.row_id,
            truck_id=row.truck_id,
            warehouse_id=row.warehouse_id,
            store_id=row.store_id,
            quantity=5,
        ),
    )
    assert result.event.reason == "plan_mismatch"
    assert simulator.observe() == before


def test_shipment_quantity_contract_rejects_zero():
    """OpenAPIのminimumとruntime検証が乖離し、0数量Actionが作られる回帰を防ぐ。"""
    with pytest.raises(ValidationError):
        ShipmentPlanRow(
            row_id="invalid",
            warehouse_id="W1",
            store_id="S1",
            quantity=0,
            truck_id="T1",
        )
