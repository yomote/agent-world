import { useState } from "react";
import { accountingApi, type AccountingRun } from "./api/accountingClient";

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
};

const yen = (value: number) => `${value.toLocaleString("ja-JP")}円`;

function packageOf(run: AccountingRun | null): PackagePayload | null {
  const artifact = run?.artifact as { payload?: unknown } | null | undefined;
  return (artifact?.payload as PackagePayload | undefined) ?? null;
}

export function AccountingLab() {
  const [run, setRun] = useState<AccountingRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const result = packageOf(run);

  async function start(mode: "agent" | "baseline") {
    setBusy(true);
    setError(null);
    try {
      let next = await accountingApi.start(mode);
      setRun(next);
      while (next.status === "running" && !next.pending_question) {
        next = await accountingApi.advance(next);
        setRun(next);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "結果不明のため停止しました");
    } finally {
      setBusy(false);
    }
  }

  async function answer(value: string) {
    if (!run?.pending_question) return;
    setBusy(true);
    setError(null);
    try {
      let next = await accountingApi.answer(run, run.pending_question.question_id, value);
      setRun(next);
      while (next.status === "running" && !next.pending_question) {
        next = await accountingApi.advance(next);
        setRun(next);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "結果不明のため停止しました");
    } finally {
      setBusy(false);
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
        <button disabled={busy} onClick={() => void start("agent")}>
          {busy && run?.mode === "agent" ? "Agentが調査中…" : "実モデルAgentで調査"}
        </button>
        <button disabled={busy} onClick={() => void start("baseline")}>
          {busy && run?.mode === "baseline" ? "固定workflow実行中…" : "同条件の固定workflow"}
        </button>
      </div>

      {error && <p className="accounting-error">{error}。自動再送していません。</p>}
      {run?.pending_question && (
        <div className="operator-question">
          <strong>経理担当への確認</strong>
          <p>{run.pending_question.question}</p>
          <small>{run.pending_question.reason}</small>
          <div>
            {run.pending_question.options.map((option) => (
              <button key={option} disabled={busy} onClick={() => void answer(option)}>
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
