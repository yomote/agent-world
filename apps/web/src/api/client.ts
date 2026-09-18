import type {
  AcceptPlanAction,
  Action,
  ActionResult,
  DispatchShipmentAction,
  EventHistory,
  LogisticsActionResult,
  LogisticsWorldState,
} from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export const worldApi = {
  observe: () => request<EventHistory>("/api/events", { cache: "no-store" }),
  act: (action: Action) =>
    request<ActionResult>("/api/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action),
    }),
};

function logisticsRequest<T>(path: string, principal: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Local-Principal": principal },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export interface LogisticsObservation {
  observe(): Promise<LogisticsWorldState>;
}

export interface OperatorCapabilities {
  reset(truckCount: number): Promise<LogisticsWorldState>;
  acceptPlan(action: AcceptPlanAction): Promise<LogisticsActionResult>;
}

export interface DispatchCapabilities {
  dispatch(action: DispatchShipmentAction): Promise<LogisticsActionResult>;
}

export const logisticsObservation: LogisticsObservation = {
  observe: () => request<LogisticsWorldState>("/api/logistics/world", { cache: "no-store" }),
};

export const operatorCapabilities: OperatorCapabilities = {
  reset: (truckCount: number) =>
    logisticsRequest<LogisticsWorldState>("/api/logistics/scenario/reset", "human-operator", {
      truck_count: truckCount,
    }),
  acceptPlan: (action: AcceptPlanAction) =>
    logisticsRequest<LogisticsActionResult>(
      "/api/logistics/plans/accept",
      "human-operator",
      action,
    ),
};

export const dispatchCapabilities: DispatchCapabilities = {
  dispatch: (action: DispatchShipmentAction) =>
    logisticsRequest<LogisticsActionResult>("/api/logistics/actions", "dispatcher", action),
};
