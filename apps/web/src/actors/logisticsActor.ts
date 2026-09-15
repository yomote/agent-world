import type { LogisticsPlan, LogisticsWorldState } from "../api/types";

type PlanRow = LogisticsPlan["rows"][number];
type StoreId = PlanRow["store_id"];
type WarehouseId = PlanRow["warehouse_id"];

export interface InventoryAllocationArtifact {
  sourceWorldId: string;
  sourceRevision: number;
  demand: { storeId: StoreId; quantity: number }[];
  supply: { warehouseId: WarehouseId; quantity: number }[];
}

export interface WarehouseScheduleArtifact {
  input: InventoryAllocationArtifact;
  shipments: Omit<PlanRow, "row_id" | "truck_id">[];
}

export interface FleetDispatchArtifact {
  input: WarehouseScheduleArtifact;
  rows: PlanRow[];
}

export interface DueDateArtifact {
  input: FleetDispatchArtifact;
  rows: PlanRow[];
  planned: number;
  unfulfilled: number;
}

function planBase(
  observation: Readonly<LogisticsWorldState>,
  createdBy: LogisticsPlan["created_by"],
  rows: PlanRow[],
  reason: string,
  handoffs: LogisticsPlan["handoffs"] = [],
): LogisticsPlan {
  return {
    plan_id: crypto.randomUUID(),
    version: createdBy === "nearest-warehouse-v1" ? 1 : 2,
    publication_state: "proposed",
    run_id: crypto.randomUUID(),
    decision_id: crypto.randomUUID(),
    input_world_id: observation.world_id,
    input_world_revision: observation.revision,
    created_by: createdBy,
    rows,
    handoffs,
    reason,
  };
}

export function allocateInventory(
  observation: Readonly<LogisticsWorldState>,
): InventoryAllocationArtifact {
  return {
    sourceWorldId: observation.world_id,
    sourceRevision: observation.revision,
    demand: observation.orders.map((order) => ({
      storeId: order.id,
      quantity: order.requested - order.fulfilled,
    })),
    supply: observation.warehouses.map((warehouse) => ({
      warehouseId: warehouse.id,
      quantity: warehouse.inventory,
    })),
  };
}

export function scheduleWarehouseCapacity(
  observation: Readonly<LogisticsWorldState>,
  input: InventoryAllocationArtifact,
): WarehouseScheduleArtifact {
  const remainingByWarehouse = new Map(
    input.supply.map((supply) => {
      const warehouse = observation.warehouses.find((item) => item.id === supply.warehouseId);
      return [supply.warehouseId, Math.min(supply.quantity, warehouse?.processing_remaining ?? 0)];
    }),
  );
  const shipments: WarehouseScheduleArtifact["shipments"] = [];
  for (const demand of input.demand) {
    let remainingDemand = demand.quantity;
    for (const warehouse of observation.warehouses) {
      const available = remainingByWarehouse.get(warehouse.id) ?? 0;
      if (remainingDemand <= 0 || available <= 0) continue;
      const quantity = Math.min(remainingDemand, available);
      shipments.push({ warehouse_id: warehouse.id, store_id: demand.storeId, quantity });
      remainingByWarehouse.set(warehouse.id, available - quantity);
      remainingDemand -= quantity;
    }
  }
  return { input, shipments };
}

export function assignFleet(
  observation: Readonly<LogisticsWorldState>,
  input: WarehouseScheduleArtifact,
): FleetDispatchArtifact {
  const rows: PlanRow[] = [];
  for (const shipment of input.shipments) {
    let remaining = shipment.quantity;
    while (remaining > 0) {
      const truck = observation.trucks[rows.length];
      if (!truck) break;
      const quantity = Math.min(remaining, truck.capacity);
      rows.push({
        row_id: `shipment-${rows.length + 1}`,
        ...shipment,
        quantity,
        truck_id: truck.id,
      });
      remaining -= quantity;
    }
  }
  return { input, rows };
}

export function coordinateDueDates(
  observation: Readonly<LogisticsWorldState>,
  input: FleetDispatchArtifact,
): DueDateArtifact {
  const rows = input.rows.filter((row) => {
    const order = observation.orders.find((item) => item.id === row.store_id);
    const road = observation.roads.find(
      (item) => item.warehouse_id === row.warehouse_id && item.store_id === row.store_id,
    );
    return Boolean(
      order && road && road.travel_ticks <= Math.min(order.due_tick, observation.horizon_ticks),
    );
  });
  const planned = rows.reduce((sum, row) => sum + row.quantity, 0);
  const requested = observation.orders.reduce(
    (sum, order) => sum + order.requested - order.fulfilled,
    0,
  );
  return { input, rows, planned, unfulfilled: requested - planned };
}

// 近い倉庫を注文ごとに選ぶ単独方策。倉庫能力の注文間競合を調整しないため比較基準になる。
export function proposeNearestWarehousePlan(
  observation: Readonly<LogisticsWorldState>,
): LogisticsPlan {
  const rows: PlanRow[] = [];
  for (const order of observation.orders) {
    const truck = observation.trucks[rows.length];
    if (!truck) break;
    const route = observation.roads
      .filter((item) => item.store_id === order.id)
      .sort((left, right) => left.travel_ticks - right.travel_ticks)[0];
    const warehouse = observation.warehouses.find((item) => item.id === route?.warehouse_id);
    if (!route || !warehouse) continue;
    const quantity = Math.min(
      order.requested - order.fulfilled,
      warehouse.processing_capacity,
      warehouse.inventory,
      truck.capacity,
    );
    if (quantity > 0)
      rows.push({
        row_id: `shipment-${rows.length + 1}`,
        warehouse_id: warehouse.id,
        store_id: order.id,
        quantity,
        truck_id: truck.id,
      });
  }
  return planBase(
    observation,
    "nearest-warehouse-v1",
    rows,
    "各注文へ最短の倉庫を割り当てる単独方策。注文間の倉庫能力競合は調整しない。",
  );
}

// 4つのpure roleが成果物を順に受け渡し、最終計画だけを提案する。
export function proposeRoleTeamPlan(observation: Readonly<LogisticsWorldState>): LogisticsPlan {
  const inventory = allocateInventory(observation);
  const schedule = scheduleWarehouseCapacity(observation, inventory);
  const dispatch = assignFleet(observation, schedule);
  const dueDate = coordinateDueDates(observation, dispatch);
  const scheduledByWarehouse = observation.warehouses.map((warehouse) => ({
    id: warehouse.id,
    quantity: schedule.shipments
      .filter((row) => row.warehouse_id === warehouse.id)
      .reduce((sum, row) => sum + row.quantity, 0),
  }));
  const handoffs: LogisticsPlan["handoffs"] = [
    {
      from_role: "inventory_allocator",
      to_role: "warehouse_scheduler",
      artifact: `需要${inventory.demand.reduce((sum, item) => sum + item.quantity, 0)} / 在庫${inventory.supply.reduce((sum, item) => sum + item.quantity, 0)}`,
    },
    {
      from_role: "warehouse_scheduler",
      to_role: "dispatcher",
      artifact: `能力配分 ${scheduledByWarehouse.map((item) => `${item.id}=${item.quantity}`).join(" / ")}`,
    },
    {
      from_role: "dispatcher",
      to_role: "due_date_coordinator",
      artifact: `配車${dispatch.rows.length}台`,
    },
    {
      from_role: "due_date_coordinator",
      to_role: "scenario_operator",
      artifact: `期内${dueDate.planned} / 未達${dueDate.unfulfilled}`,
    },
  ];
  return planBase(
    observation,
    "role-team-v1",
    dueDate.rows,
    "在庫配分→倉庫能力→配車→納期確認の成果物を順に受け渡し、全体能力を配分する。",
    handoffs,
  );
}
