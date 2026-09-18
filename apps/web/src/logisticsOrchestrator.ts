import { proposeNearestWarehousePlan, proposeRoleTeamPlan } from "./actors/logisticsActor";
import type { DispatchCapabilities, OperatorCapabilities } from "./api/client";
import type {
  DispatchShipmentAction,
  LogisticsEvent,
  LogisticsPlan,
  LogisticsWorldState,
} from "./api/types";

export interface RunArtifact {
  label: string;
  plan: LogisticsPlan;
  events: LogisticsEvent[];
  actual: number;
  requested: number;
  unfulfilled: number;
  finalRevision: number;
  finalWorld: LogisticsWorldState;
  status: "confirmed";
  acceptance: "auto_accepted" | "not_accepted";
  acceptanceReason: LogisticsEvent["reason"];
}

interface RunOptions {
  label: string;
  truckCount: number;
  strategy: "nearest" | "team";
  operator: OperatorCapabilities;
  dispatcher: DispatchCapabilities;
}

// Hostだけがcapabilityを持ち、pure Actorの提案を採用・直列実行する。
export async function runLogisticsScenario(options: RunOptions): Promise<RunArtifact> {
  let current = await options.operator.reset(options.truckCount);
  const plan =
    options.strategy === "nearest"
      ? proposeNearestWarehousePlan(current)
      : proposeRoleTeamPlan(current);
  const accepted = await options.operator.acceptPlan({
    action_id: crypto.randomUUID(),
    run_id: plan.run_id,
    decision_id: plan.decision_id,
    actor_id: "scenario-operator",
    type: "accept_logistics_plan",
    expected_revision: current.revision,
    plan,
  });
  current = accepted.world;
  const events = [accepted.event];
  if (accepted.event.status === "success") {
    for (const row of plan.rows) {
      const action: DispatchShipmentAction = {
        action_id: crypto.randomUUID(),
        run_id: plan.run_id,
        decision_id: plan.decision_id,
        actor_id: "logistics-dispatcher",
        type: "dispatch_shipment",
        expected_revision: current.revision,
        plan_id: plan.plan_id,
        plan_version: plan.version,
        row_id: row.row_id,
        truck_id: row.truck_id,
        warehouse_id: row.warehouse_id,
        store_id: row.store_id,
        quantity: row.quantity,
      };
      const result = await options.dispatcher.dispatch(action);
      current = result.world;
      events.push(result.event);
    }
  }
  const actual = current.orders.reduce((sum, order) => sum + order.fulfilled, 0);
  const requested = current.orders.reduce((sum, order) => sum + order.requested, 0);
  return {
    label: options.label,
    plan,
    events,
    actual,
    requested,
    unfulfilled: requested - actual,
    finalRevision: current.revision,
    finalWorld: current,
    status: "confirmed",
    acceptance: accepted.event.status === "success" ? "auto_accepted" : "not_accepted",
    acceptanceReason: accepted.event.reason,
  };
}
