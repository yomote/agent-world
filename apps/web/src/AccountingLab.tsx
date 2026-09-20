import { useEffect, useState } from "react";
import { comparisonMessage } from "./accountingComparison";
import { AccountingApiError, accountingApi, type AccountingRun } from "./api/accountingClient";

type PackagePayload = {
  actual_ledger_updated: false;
  proposal: {
    allocations: Array<{
      receipt_id: string;
      invoice_id: string;
      cash_amount: { minor_units: number };
      evidence: Array<{ artifact_id: string; version: number; span: string }>;
    }>;
    unapplied: { minor_units: number };
    questions: string[];
  };
  validation: {
    allocated_cash: { minor_units: number };
    planned_balance: { minor_units: number };
    ledger_balance_unchanged: boolean;
  };
  sales_inquiry: string[];
  observed_adjustments: Array<{
    adjustment_id: string;
    amount: { minor_units: number };
    status: string;
    source: { artifact_id: string; version: number; span: string };
  }>;
};

const yen = (value: number) => `${value.toLocaleString("ja-JP")}円`;

export function accountingCaseLabel(run: Pick<AccountingRun, "fixture_id">) {
  const labels: Record<string, string> = {
    "fixture-9d5e33713165": "入金と請求の差額・取消を調べた公開例",
    "fixture-3dff6a560d89": "同額請求2件で確認を保留した例",
  };
  return labels[run.fixture_id] ?? "保存済みの調査例";
}

export function accountingStateLabel(run: Pick<AccountingRun, "status" | "pending_question">) {
  if (run.pending_question) return "人の確認待ち・成果物なし";
  if (run.status === "ready_for_review") return "レビュー用成果物あり";
  return `停止状態: ${run.status}`;
}

function packageOf(run: AccountingRun | null): PackagePayload | null {
  const artifact = run?.artifact as { payload?: unknown } | null | undefined;
  return (artifact?.payload as PackagePayload | undefined) ?? null;
}

export function AccountingLab() {
  const [runs, setRuns] = useState<Record<"agent" | "baseline", AccountingRun | null>>({
    agent: null,
    baseline: null,
  });
  const [history, setHistory] = useState<AccountingRun[]>([]);
  const [selectedMode, setSelectedMode] = useState<"agent" | "baseline">("agent");
  const [busyMode, setBusyMode] = useState<"agent" | "baseline" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = runs[selectedMode];
  const result = packageOf(run);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const summaries = await accountingApi.list();
        const storedRuns = await Promise.all(
          summaries.map((summary) => accountingApi.get(summary.run_id)),
        );
        const latest = (["agent", "baseline"] as const).map(
          (mode) => [mode, storedRuns.find((item) => item.mode === mode) ?? null] as const,
        );
        if (active) setHistory(storedRuns);
        if (active) setRuns(Object.fromEntries(latest) as typeof runs);
      } catch {
        // 履歴表示はread-only補助。新規実行やwrite retryへ連鎖させない。
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  function remember(mode: "agent" | "baseline", value: AccountingRun) {
    setRuns((current) => ({ ...current, [mode]: value }));
    setHistory((current) => [value, ...current.filter((item) => item.run_id !== value.run_id)]);
  }

  async function reportFailure(
    mode: "agent" | "baseline",
    current: AccountingRun | null,
    reason: unknown,
  ) {
    if (!current) {
      const kind = reason instanceof AccountingApiError ? "domain/API失敗" : "通信失敗";
      setError(`${kind}: runを作成できず状態は未確認です`);
      return;
    }
    try {
      const observed = await accountingApi.get(current.run_id);
      remember(mode, observed);
      const labels: Record<string, string> = {
        unknown_terminal: "結果不明で停止",
        budget_exhausted: "予算上限で停止",
        domain_failed: "domain検証失敗",
      };
      setError(labels[observed.status] ?? `通信失敗（read-only照合時の状態: ${observed.status}）`);
    } catch {
      setError("通信失敗・状態未確認です。writeは自動再送していません");
    }
  }

  async function start(mode: "agent" | "baseline") {
    setSelectedMode(mode);
    setBusyMode(mode);
    setError(null);
    let current: AccountingRun | null = null;
    try {
      let next = await accountingApi.start(mode);
      current = next;
      remember(mode, next);
      while (next.status === "running" && !next.pending_question) {
        next = await accountingApi.advance(next);
        current = next;
        remember(mode, next);
      }
    } catch (reason) {
      await reportFailure(mode, current, reason);
    } finally {
      setBusyMode(null);
    }
  }

  async function answer(value: string) {
    if (!run?.pending_question) return;
    setBusyMode(selectedMode);
    setError(null);
    let current: AccountingRun | null = run;
    try {
      let next = await accountingApi.answer(run, run.pending_question.question_id, value);
      current = next;
      remember(selectedMode, next);
      while (next.status === "running" && !next.pending_question) {
        next = await accountingApi.advance(next);
        current = next;
        remember(selectedMode, next);
      }
    } catch (reason) {
      await reportFailure(selectedMode, current, reason);
    } finally {
      setBusyMode(null);
    }
  }

  return (
    <section className="accounting-lab" aria-labelledby="accounting-title">
      <div className="lab-heading">
        <div>
          <p className="eyebrow">BUSINESS AGENT / CASH APPLICATION</p>
          <h2 id="accounting-title">入金消込のレビュー準備</h2>
        </div>
        <span className="scope-badge">合成データ · 台帳非更新 · ローカル実験</span>
      </div>

      <div className="accounting-intro">
        <h3>何が楽になるデモ？</h3>
        <p>
          「消込」は、入金をどの請求に対応させるかを決める作業です。経理担当が銀行明細、
          請求書、メール、調整履歴を探し直す代わりに、Agentが資料を選んで調べ、根拠付きの確認用資料を作ります。
        </p>
        <ol>
          <li>公開例の資料と条件を確認</li>
          <li>Agent調査、または同条件の固定workflowを開始</li>
          <li>質問が出た場合だけ回答</li>
          <li>判明した配分、未確認、営業へ渡す確認事項と根拠を見る</li>
        </ol>
        <details>
          <summary>公開例と成功・失敗の見方</summary>
          <p>
            70,000円の請求に50,000円が入金され、20,000円の値引きは取消済みです。成功は50,000円だけを配分案にし、
            残り20,000円を未回収として渡すこと。取消済み値引きを足して全額支払済みにすると失敗です。
            hidden評価caseはこの公開例と分離しています。
          </p>
        </details>
      </div>

      {history.length > 0 && (
        <div className="accounting-history">
          <label htmlFor="accounting-history-run">保存済みの調査結果を見る</label>
          <select
            id="accounting-history-run"
            value={run?.run_id ?? ""}
            onChange={(event) => {
              const stored = history.find((item) => item.run_id === event.target.value);
              if (!stored) return;
              const mode = stored.mode === "agent" ? "agent" : "baseline";
              setSelectedMode(mode);
              remember(mode, stored);
              setError(null);
            }}
          >
            {history.map((item) => (
              <option key={item.run_id} value={item.run_id}>
                {accountingCaseLabel(item)} /{" "}
                {item.mode === "agent" ? "実モデル保存結果" : "固定workflow"} /{" "}
                {accountingStateLabel(item)}
              </option>
            ))}
          </select>
          <strong>
            これは保存済みの実行履歴です。選択しても新規実行やモデル呼出しは行いません。
          </strong>
          <small>合成データだけを使い、実台帳は更新していません。</small>
          {run && (
            <details>
              <summary>技術情報</summary>
              <code>
                {run.fixture_id} / {run.status} / {run.run_id}
              </code>
            </details>
          )}
        </div>
      )}

      <details className="accounting-new-run">
        <summary>新しく実行する（保存結果を見るだけなら操作不要）</summary>
        <div className="accounting-actions">
          <button disabled={busyMode !== null} onClick={() => void start("agent")}>
            {busyMode === "agent" ? "Agentが調査中…" : "実モデルAgentで調査"}
          </button>
          <button disabled={busyMode !== null} onClick={() => void start("baseline")}>
            {busyMode === "baseline" ? "固定workflow実行中…" : "固定workflowを実行"}
          </button>
        </div>
      </details>

      {error && <p className="accounting-error">{error}。自動再送していません。</p>}
      {(runs.agent || runs.baseline) && (
        <div className="accounting-comparison" aria-label="実行結果比較">
          {(["agent", "baseline"] as const).map((mode) => {
            const compared = runs[mode];
            const comparedPackage = packageOf(compared);
            return (
              <button
                className={selectedMode === mode ? "selected" : ""}
                disabled={!compared}
                key={mode}
                onClick={() => setSelectedMode(mode)}
              >
                <strong>{mode === "agent" ? "実モデルAgent" : "固定workflow"}</strong>
                {compared ? (
                  <>
                    <span>状態 {compared.status}</span>
                    <span>
                      判断 {compared.model_attempts} / tool {compared.tool_calls}
                    </span>
                    <span>fixture {compared.fixture_id}</span>
                    {comparedPackage ? (
                      <span>
                        配分 {yen(comparedPackage.validation.allocated_cash.minor_units)} / 未配分{" "}
                        {yen(comparedPackage.proposal.unapplied.minor_units)}
                      </span>
                    ) : (
                      <span>成果物なし</span>
                    )}
                  </>
                ) : (
                  <span>未実行</span>
                )}
              </button>
            );
          })}
          <p>{comparisonMessage(runs.agent, runs.baseline)}</p>
        </div>
      )}
      {run?.pending_question && (
        <div className="operator-question">
          <strong>経理担当への確認</strong>
          <p>{run.pending_question.question}</p>
          <small>{run.pending_question.reason}</small>
          <div>
            {run.pending_question.options.map((option) => (
              <button key={option} disabled={busyMode !== null} onClick={() => void answer(option)}>
                {option}
              </button>
            ))}
          </div>
        </div>
      )}

      {run && (
        <div className="accounting-status" role="status">
          <span>状態: {run.status}</span>
          <span>判断 {run.model_attempts}/11</span>
          <span>tool {run.tool_calls}/18</span>
          <span>step {run.step_version}</span>
          <span>{run.mode === "agent" ? "actual model" : "fixed workflow"}</span>
        </div>
      )}

      {result && (
        <div className="review-package">
          <h3>経理レビューpackage</h3>
          <div className="package-kpis">
            <div>
              <span>配分案</span>
              <strong>{yen(result.validation.allocated_cash.minor_units)}</strong>
            </div>
            <div>
              <span>配分後の予定残高</span>
              <strong>{yen(result.validation.planned_balance.minor_units)}</strong>
            </div>
            <div>
              <span>未配分</span>
              <strong>{yen(result.proposal.unapplied.minor_units)}</strong>
            </div>
            <div>
              <span>実台帳</span>
              <strong>{result.validation.ledger_balance_unchanged ? "変更なし" : "要確認"}</strong>
            </div>
          </div>
          {result.proposal.allocations.map((line) => (
            <article key={`${line.receipt_id}-${line.invoice_id}`}>
              <strong>
                {line.receipt_id} → {line.invoice_id}: {yen(line.cash_amount.minor_units)}
              </strong>
              <p>
                根拠:{" "}
                {line.evidence
                  .map((ref) => `${ref.artifact_id} v${ref.version}#${ref.span}`)
                  .join(" / ")}
              </p>
            </article>
          ))}
          <h4>確認した調整</h4>
          {(result.observed_adjustments ?? []).length ? (
            <ul>
              {(result.observed_adjustments ?? []).map((adjustment) => (
                <li key={adjustment.adjustment_id}>
                  {adjustment.adjustment_id}: {yen(adjustment.amount.minor_units)} /{" "}
                  {adjustment.status}
                  （根拠 {adjustment.source.artifact_id} v{adjustment.source.version}#
                  {adjustment.source.span}）
                </li>
              ))}
            </ul>
          ) : (
            <p>調整履歴は未確認です。</p>
          )}
          <h4>未確認・次担当</h4>
          {result.sales_inquiry.length ? (
            <ul>
              {result.sales_inquiry.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : (
            <p>追加の営業確認なし</p>
          )}
          <p className="scope-note">
            これはレビュー待ちの提案です。帳簿確定、資金移動、外部送信は行いません。
          </p>
        </div>
      )}

      {run && (
        <details className="accounting-trace">
          <summary>実行traceを見る（{run.trace.length}件）</summary>
          <ol>
            {run.trace.map((item, index) => {
              const entry = item as {
                kind?: string;
                name?: string;
                decision_id?: string;
                tool_call_id?: string;
              };
              return (
                <li key={`${entry.kind}-${entry.name}-${index}`}>
                  <code>
                    {entry.kind} / {entry.name}
                  </code>
                </li>
              );
            })}
          </ol>
        </details>
      )}
    </section>
  );
}
