import { describe, expect, it } from "vitest";
import type { LogisticsWorldState } from "../api/types";
import {
  allocateInventory,
  assignFleet,
  coordinateDueDates,
  proposeNearestWarehousePlan,
  proposeRoleTeamPlan,
  scheduleWarehouseCapacity,
} from "./logisticsActor";

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
  trucks: [
    { id: "T1", capacity: 8, available_deliveries: 1 },
    { id: "T2", capacity: 8, available_deliveries: 1 },
    { id: "T3", capacity: 8, available_deliveries: 1 },
  ],
  roads: [
    { warehouse_id: "W1", store_id: "S1", travel_ticks: 1 },
    { warehouse_id: "W1", store_id: "S2", travel_ticks: 1 },
    { warehouse_id: "W2", store_id: "S1", travel_ticks: 2 },
    { warehouse_id: "W2", store_id: "S2", travel_ticks: 2 },
  ],
  accepted_plan: null,
});

describe("物流Actorのpure proposal", () => {
  // Actorが観測を直接更新したり、倉庫間調整済みのふりをする回帰を防ぐ。
  it("単独方策は近接倉庫へ集中する計画だけを返す", () => {
    const observation = structuredClone(world());
    const before = structuredClone(observation);
    const plan = proposeNearestWarehousePlan(observation);
    expect(plan.rows.map((row) => [row.warehouse_id, row.store_id, row.quantity])).toEqual([
      ["W1", "S1", 4],
      ["W1", "S2", 4],
    ]);
    expect(plan.handoffs).toEqual([]);
    expect(observation).toEqual(before);
  });

  // 役割間の成果物が実入力にならず、説明ラベルだけのpipelineになる回帰を防ぐ。
  it("各pure roleが前段artifactを入力として能力・車両・期限を順に反映する", () => {
    const observation = world();
    const inventory = allocateInventory(observation);
    const schedule = scheduleWarehouseCapacity(observation, inventory);
    const fleet = assignFleet(observation, schedule);
    const dueDate = coordinateDueDates(observation, fleet);
    expect(schedule.input).toBe(inventory);
    expect(fleet.input).toBe(schedule);
    expect(dueDate.input).toBe(fleet);
    expect(schedule.shipments.map((row) => [row.warehouse_id, row.quantity])).toEqual([
      ["W1", 4],
      ["W2", 4],
      ["W2", 8],
    ]);
    expect(fleet.rows.map((row) => row.truck_id)).toEqual(["T1", "T2", "T3"]);
    expect(dueDate).toMatchObject({ planned: 16, unfulfilled: 0 });
  });

  // pipeline出力と表示handoffが別計算になり、実計画の来歴を誤表示する回帰を防ぐ。
  it("役割方策はpipeline出力をplanとhandoffへ変換する", () => {
    const plan = proposeRoleTeamPlan(world());
    expect(plan.rows.map((row) => [row.warehouse_id, row.store_id, row.quantity])).toEqual([
      ["W1", "S1", 4],
      ["W2", "S1", 4],
      ["W2", "S2", 8],
    ]);
    expect(plan.handoffs.map((handoff) => handoff.artifact)).toEqual([
      "需要16 / 在庫32",
      "能力配分 W1=4 / W2=12",
      "配車3台",
      "期内16 / 未達0",
    ]);
  });
});
