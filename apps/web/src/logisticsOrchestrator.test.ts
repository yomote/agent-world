import { describe, expect, it, vi } from "vitest";
import type { OperatorCapabilities } from "./api/client";
import type { LogisticsWorldState } from "./api/types";
import { runLogisticsScenario } from "./logisticsOrchestrator";

const world = (): LogisticsWorldState => ({
  world_id: "36e9fc42-804f-4a9d-b22c-ae919090d5ea",
  revision: 0,
  horizon_ticks: 2,
  warehouses: [
    { id: "W1", inventory: 16, processing_capacity: 4, processing_remaining: 4 },
    { id: "W2", inventory: 16, processing_capacity: 12, processing_remaining: 12 },
  ],
  orders: [
    { id: "S1", requested: 8, fulfilled: 0, due_tick: 2 },
    { id: "S2", requested: 8, fulfilled: 0, due_tick: 2 },
  ],
  trucks: [{ id: "T1", capacity: 8, available_deliveries: 1 }],
  roads: [
    { warehouse_id: "W1", store_id: "S1", travel_ticks: 1 },
    { warehouse_id: "W1", store_id: "S2", travel_ticks: 1 },
    { warehouse_id: "W2", store_id: "S1", travel_ticks: 2 },
    { warehouse_id: "W2", store_id: "S2", travel_ticks: 2 },
  ],
  accepted_plan: null,
});

describe("物流orchestrator", () => {
  // plan採用failure後にdispatchし、未採用planをaccepted表示する回帰を防ぐ。
  it("accept failureならdispatchせずnot accepted artifactを返す", async () => {
    const initial = world();
    const operator: OperatorCapabilities = {
      reset: async () => initial,
      acceptPlan: async (action) => ({
        world: initial,
        event: {
          event_id: crypto.randomUUID(),
          action_id: action.action_id,
          run_id: action.run_id,
          decision_id: action.decision_id,
          actor_id: action.actor_id,
          action_type: action.type,
          status: "domain_failure",
          reason: "stale_revision",
          principal_id: "human-operator",
          principal_kind: "human",
          principal_role: "scenario_operator",
          plan_id: action.plan.plan_id,
          plan_version: action.plan.version,
          row_id: null,
          quantity: 0,
          world_id: initial.world_id,
          world_revision: initial.revision,
        },
      }),
    };
    const dispatch = vi.fn();
    const artifact = await runLogisticsScenario({
      label: "failure",
      truckCount: 1,
      strategy: "nearest",
      operator,
      dispatcher: { dispatch },
    });
    expect(dispatch).not.toHaveBeenCalled();
    expect(artifact).toMatchObject({
      acceptance: "not_accepted",
      acceptanceReason: "stale_revision",
      actual: 0,
      finalRevision: 0,
    });
  });
});
