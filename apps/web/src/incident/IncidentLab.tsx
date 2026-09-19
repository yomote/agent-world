import { useState } from "react";

type AgentRun = {
  run_id: string;
  scenario_number: number;
  status: string;
  mode: "actual" | "baseline";
  provider: string;
  model: string;
  model_calls: number;
  model_successes: number;
  model_failures: number;
  global_model_calls_remaining: number | null;
  tool_calls: number;
  clarification_count: number;
  proposal_count: number;
  latest_reason: string;
  trace: Array<{
    sequence: number;
    kind: string;
    name: string;
    public_reason: string;
    evidence_refs: string[];
    payload: Record<string, unknown>;
  }>;
  world: {
    summary: {
      revision: number;
      accepted: number;
      incomplete: number;
      completed: number;
      duplicate_count: number;
      state: string;
    };
    services: Record<string, { health: string; pending?: number; dead_letter?: number }>;
  };
  pending_question: { question?: string; reason?: string; options?: string[] } | null;
  pending_proposal: {
    diagnosis: string;
    changes: Array<Record<string, unknown>>;
    rollback: string;
  } | null;
  proposal_hash: string | null;
  execution_action_id: string | null;
  artifacts: Record<string, unknown>;
  error: string | null;
};

async function send(path: string, body?: unknown): Promise<AgentRun> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<AgentRun>;
}

const statusLabel: Record<string, string> = {
  observing: "観測開始前",
  investigating: "調査中",
  waiting_human: "業務判断待ち",
  proposing: "復旧案作成中",
  waiting_approval: "実行承認待ち",
  applying: "適用中",
  verifying: "回復検証中",
  recovered: "回復確認済み",
  stopped: "停止",
  unknown: "結果不明",
};

export function IncidentLab() {
  const [scenario, setScenario] = useState(1);
  const [mode, setMode] = useState<"actual" | "baseline">("actual");
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function mutate(work: () => Promise<AgentRun>) {
    setBusy(true);
    setError(null);
    try {
      setRun(await work());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "通信結果不明");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="incident-lab" aria-labelledby="incident-title">
      <header className="incident-hero">
        <div>
          <p className="eyebrow">AGENTIC INCIDENT RECOVERY LAB</p>
          <h2 id="incident-title">止まった発送指示を、根拠から安全に復旧する</h2>
          <p className="incident-mission">
            受付は成功していますが、発送指示が10分間進んでいません。原因を調べ、変更前に承認を求め、漏れと二重処理を検証します。
          </p>
        </div>
        <div className={`mode-badge ${mode}`}>
          {mode === "actual" ? "Actual model" : "Runbook baseline"}
          <small>Simulated services</small>
        </div>
      </header>

      <div className="incident-controls">
        <label>
          障害番号{" "}
          <select
            value={scenario}
            disabled={busy || Boolean(run)}
            onChange={(event) => setScenario(Number(event.target.value))}
          >
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
        <label>
          方式{" "}
          <select
            value={mode}
            disabled={busy || Boolean(run)}
            onChange={(event) => setMode(event.target.value as "actual" | "baseline")}
          >
            <option value="actual">実モデルAgent</option>
            <option value="baseline">決定的runbook</option>
          </select>
        </label>
        <button
          disabled={busy || Boolean(run)}
          onClick={() =>
            void mutate(() => send("/agent/runs", { scenario_number: scenario, mode }))
          }
        >
          Labを初期化
        </button>
        {run && (
          <button
            className="primary-action"
            disabled={busy || !["observing", "investigating", "verifying"].includes(run.status)}
            onClick={() => void mutate(() => send(`/agent/runs/${run.run_id}/advance`))}
          >
            {busy ? "判断中…" : "Agent判断loopを実行"}
          </button>
        )}
        {run && (
          <button
            disabled={busy}
            onClick={() => {
              setRun(null);
              setError(null);
            }}
          >
            別の障害
          </button>
        )}
        {run?.status === "unknown" && (
          <button
            disabled={busy}
            onClick={() => void mutate(() => send(`/agent/runs/${run.run_id}/reconcile`))}
          >
            action statusを照合
          </button>
        )}
      </div>
      {error && (
        <div className="error" role="alert">
          通信結果不明: {error}。自動再送していません。
        </div>
      )}
      {!run ? (
        <p className="incident-empty">
          番号だけを選び、同じ症状から開始します。原因名や期待解はAgentへ渡りません。
        </p>
      ) : (
        <>
          <div className="metric-strip">
            <div>
              <span>状態</span>
              <strong>{statusLabel[run.status] ?? run.status}</strong>
            </div>
            <div>
              <span>未完了</span>
              <strong>{run.world.summary.incomplete}</strong>
            </div>
            <div>
              <span>完了</span>
              <strong>{run.world.summary.completed}</strong>
            </div>
            <div>
              <span>重複</span>
              <strong>{run.world.summary.duplicate_count}</strong>
            </div>
            <div>
              <span>calls</span>
              <strong>
                {run.model_calls} model ({run.model_successes} ok / {run.model_failures} fail) /{" "}
                {run.tool_calls} tool
              </strong>
            </div>
          </div>
          <div className="incident-grid">
            <section className="incident-card">
              <h3>Simulated services</h3>
              <ul className="service-list">
                {Object.entries(run.world.services).map(([name, service]) => (
                  <li key={name}>
                    <strong>{name}</strong>
                    <span className={`health ${service.health}`}>{service.health}</span>
                    <small>
                      {service.pending !== undefined
                        ? `pending ${service.pending} / DLQ ${service.dead_letter}`
                        : ""}
                    </small>
                  </li>
                ))}
              </ul>
              <dl className="run-meta">
                <div>
                  <dt>provider</dt>
                  <dd>{run.provider}</dd>
                </div>
                <div>
                  <dt>model</dt>
                  <dd>{run.model}</dd>
                </div>
                <div>
                  <dt>run</dt>
                  <dd title={run.run_id}>{run.run_id.slice(0, 8)}</dd>
                </div>
                <div>
                  <dt>revision</dt>
                  <dd>{run.world.summary.revision}</dd>
                </div>
              </dl>
            </section>
            <section className="incident-card decision-card">
              <h3>公開中の判断</h3>
              <p className="latest-reason">{run.latest_reason}</p>
              {run.pending_question && (
                <div className="human-gate">
                  <strong>業務判断</strong>
                  <p>{run.pending_question.question}</p>
                  <small>{run.pending_question.reason}</small>
                  <div>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void mutate(() =>
                          send(`/agent/runs/${run.run_id}/answer`, { answer: "use_spare" }),
                        )
                      }
                    >
                      このrunだけ予備経路
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void mutate(() =>
                          send(`/agent/runs/${run.run_id}/answer`, { answer: "wait_primary" }),
                        )
                      }
                    >
                      primaryを待つ
                    </button>
                  </div>
                </div>
              )}
              {run.pending_proposal && (
                <div className="human-gate">
                  <strong>実行承認</strong>
                  <p>{run.pending_proposal.diagnosis}</p>
                  <pre>{JSON.stringify(run.pending_proposal.changes, null, 2)}</pre>
                  <small>rollback: {run.pending_proposal.rollback}</small>
                  <div>
                    <button
                      className="primary-action"
                      disabled={busy}
                      onClick={() =>
                        void mutate(() =>
                          send(`/agent/runs/${run.run_id}/approval`, {
                            approve: true,
                            approver: "local-operator",
                          }),
                        )
                      }
                    >
                      差分を承認して適用
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        void mutate(() =>
                          send(`/agent/runs/${run.run_id}/approval`, {
                            approve: false,
                            approver: "local-operator",
                          }),
                        )
                      }
                    >
                      却下
                    </button>
                  </div>
                </div>
              )}
              {run.error && <p className="failure-text">{run.error}</p>}
            </section>
          </div>
          <section className="incident-card evidence-card">
            <div className="panel-heading">
              <h3>判断とEvidenceの時系列</h3>
              <span>hidden chain-of-thoughtは保存しません</span>
            </div>
            {run.trace.length === 0 ? (
              <p className="incident-empty">
                Agent判断loopを実行すると、model選択とtool結果がここに並びます。
              </p>
            ) : (
              <ol className="evidence-list">
                {[...run.trace].reverse().map((entry) => (
                  <li key={entry.sequence}>
                    <span className={`trace-kind ${entry.kind}`}>{entry.kind}</span>
                    <div>
                      <strong>
                        {entry.sequence}. {entry.name}
                      </strong>
                      <p>{entry.public_reason}</p>
                      <small>{entry.evidence_refs.join(" · ") || "evidence待ち"}</small>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>
          {run.status === "recovered" && (
            <section className="incident-card">
              <h3>引継ぎArtifact</h3>
              <pre>{JSON.stringify(run.artifacts, null, 2)}</pre>
            </section>
          )}
        </>
      )}
      <footer className="incident-note">
        ローカル合成serviceのみ。実モデルは観測から次toolを選択し、World
        Simulatorだけが状態を更新します。永続化・本番認証・本番service接続はありません。
      </footer>
    </section>
  );
}
