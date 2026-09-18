import type { components } from "./accounting-schema";

export type AccountingRun = components["schemas"]["RunView"];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Accounting API ${response.status}: ${detail}`);
  }
  return (await response.json()) as T;
}

export const accountingApi = {
  start(mode: "agent" | "baseline") {
    return request<AccountingRun>("/api/accounting/runs", {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
  },
  advance(run: AccountingRun) {
    return request<AccountingRun>(`/api/accounting/runs/${run.run_id}/advance`, {
      method: "POST",
      body: JSON.stringify({
        action_id: crypto.randomUUID(),
        expected_step_version: run.step_version,
      }),
    });
  },
  answer(run: AccountingRun, questionId: string, answer: string) {
    return request<AccountingRun>(`/api/accounting/runs/${run.run_id}/answer`, {
      method: "POST",
      body: JSON.stringify({
        action_id: crypto.randomUUID(),
        expected_step_version: run.step_version,
        question_id: questionId,
        answer,
      }),
    });
  },
};
