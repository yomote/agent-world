import { useState } from "react";
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

function packageOf(run: AccountingRun | null): PackagePayload | null {
  const artifact = run?.artifact as { payload?: unknown } | null | undefined;
  return (artifact?.payload as PackagePayload | undefined) ?? null;
}

export function AccountingLab() {
  const [runs, setRuns] = useState<Record<"agent" | "baseline", AccountingRun | null>>({
    agent: null,
    baseline: null,
  });
  const [selectedMode, setSelectedMode] = useState<"agent" | "baseline">("agent");
  const [busyMode, setBusyMode] = useState<"agent" | "baseline" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = runs[selectedMode];
  const result = packageOf(run);

  function remember(mode: "agent" | "baseline", value: AccountingRun) {
    setRuns((current) => ({ ...current, [mode]: value }));
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
          「消込」は、銀行へ入ったお金を未払いの請求へ対応づける作業です。経理担当が銀行明細、
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

      <div className="accounting-actions">
        <button disabled={busyMode !== null} onClick={() => void start("agent")}>
          {busyMode === "agent" ? "Agentが調査中…" : "実モデルAgentで調査"}
        </button>
        <button disabled={busyMode !== null} onClick={() => void start("baseline")}>
          {busyMode === "baseline" ? "固定workflow実行中…" : "同条件の固定workflow"}
        </button>
      </div>

      {error && <p className="accounting-error">{error}。自動再送していません。</p>}
      {(runs.agent || runs.baseline) && (
        <div className="accounting-comparison" aria-label="同条件の実行結果比較">
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
          <p>
            優位性は未実証です。同じ資料・typed fact・solver・validatorで結果と負担を比較します。
          </p>
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
