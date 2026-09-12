import type { Action, ActionResult, EventHistory } from "./types";

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
