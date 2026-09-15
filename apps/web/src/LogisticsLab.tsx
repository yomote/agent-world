import { useEffect, useState } from "react";
import { dispatchCapabilities, logisticsObservation, operatorCapabilities } from "./api/client";
import type { LogisticsEvent, LogisticsWorldState } from "./api/types";
import { runLogisticsScenario, type RunArtifact } from "./logisticsOrchestrator";

const reasonLabels: Record<LogisticsEvent["reason"], string> = {
  plan_accepted: "計画を採用",
  shipment_dispatched: "配送を確定",
  authz_denied: "認可拒否",
  stale_revision: "観測revisionが古い",
  wrong_world: "別Worldの計画",
  plan_mismatch: "採用計画と不一致",
  truck_unavailable: "トラックが使用済み",
  truck_capacity_exceeded: "積載上限超過",
  warehouse_capacity_exceeded: "倉庫処理能力を超過",
  insufficient_inventory: "在庫不足",
  order_already_fulfilled: "注文充足済み",
  order_quantity_exceeded: "注文残を超過",
  route_unavailable: "配送路なし",
  arrival_after_horizon: "期限後に到着",
};

export function LogisticsLab() {
  const [world, setWorld] = useState<LogisticsWorldState | null>(null);
  const [runs, setRuns] = useState<RunArtifact[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("固定Scenarioを読み込んでいます…");

  useEffect(() => {
    void logisticsObservation
      .observe()
      .then((observed) => {
        setWorld(observed);
        setMessage("人間の介入を選んで比較を始められます。");
      })
      .catch((error: unknown) => setMessage(`通信失敗・結果不明: ${String(error)}`));
  }, []);

  async function runScenario(label: string, truckCount: number, strategy: "nearest" | "team") {
    if (busy) return;
    setBusy(true);
    setMessage(`${label}: Worldを同じ固定条件で再生成しています…`);
    try {
      const artifact = await runLogisticsScenario({
        label,
        truckCount,
        strategy,
        operator: operatorCapabilities,
        dispatcher: dispatchCapabilities,
      });
      setWorld(artifact.finalWorld);
      setRuns((previous) => [artifact, ...previous.filter((item) => item.label !== label)]);
      setMessage(`${label}: Simulatorの確定結果を受け取りました。`);
    } catch (error) {
      setMessage(`通信失敗・結果不明（自動再送なし）: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  const delivered = world?.orders.reduce((sum, order) => sum + order.fulfilled, 0) ?? 0;
  const requested = world?.orders.reduce((sum, order) => sum + order.requested, 0) ?? 16;

  return (
    <section className="logistics-lab" aria-labelledby="logistics-title">
      <div className="lab-heading">
        <div>
          <p className="eyebrow">LOGISTICS WORLD / MVP</p>
          <h2 id="logistics-title">トラックを増やすだけで、納品は増えるか？</h2>
        </div>
        <div className="lab-kpi" aria-label="現在の確定納品数">
          <strong>{delivered}</strong> / {requested} units
          <span>Simulator actual</span>
        </div>
      </div>

      <p className="lab-intro">
        在庫16ずつ、注文8ずつ。W1の処理上限は4、W2は12です。各実験はWorld
        APIで同じ初期条件へ戻し、計画ではなく確定配送から納品数を集計します。
      </p>

      <div className="intervention-buttons" aria-label="物流Scenarioの介入">
        <button disabled={busy} onClick={() => void runScenario("A: トラック1台", 1, "nearest")}>
          A: 1台で近い倉庫へ
        </button>
        <button disabled={busy} onClick={() => void runScenario("B: 3台・計画据置", 3, "nearest")}>
          B: トラックを3台へ増加
        </button>
        <button
          className="primary"
          disabled={busy}
          onClick={() => void runScenario("C: 3台・再配分", 3, "team")}
        >
          C: 役割分担で再配分
        </button>
      </div>
      <p className="lab-message" role="status">
        {message}
      </p>

      {world && (
        <div className="logistics-world">
          <div>
            <h3>倉庫</h3>
            {world.warehouses.map((warehouse) => (
              <p key={warehouse.id}>
                <strong>{warehouse.id}</strong> 在庫 {warehouse.inventory} / 処理残{" "}
                {warehouse.processing_remaining}
              </p>
            ))}
          </div>
          <div>
            <h3>店舗注文</h3>
            {world.orders.map((order) => (
              <p key={order.id}>
                <strong>{order.id}</strong> 確定 {order.fulfilled} / 注文 {order.requested}
              </p>
            ))}
          </div>
          <div>
            <h3>利用可能トラック</h3>
            <p>
              {world.trucks.filter((truck) => truck.available_deliveries > 0).length} /{" "}
              {world.trucks.length} 台
            </p>
            <p className="muted">
              world {world.world_id.slice(0, 8)} · rev {world.revision}
            </p>
          </div>
        </div>
      )}

      <div className="run-grid">
        {runs.map((run) => (
          <article className="run-card" key={run.label}>
            <div className="run-title">
              <div>
                <span className="artifact-state">
                  plan v{run.plan.version} · proposed → accepted
                </span>
                <h3>{run.label}</h3>
              </div>
              <strong className={run.actual === run.requested ? "complete" : "partial"}>
                {run.actual}/{run.requested}
              </strong>
            </div>
            <p>{run.plan.reason}</p>
            <ol className="shipment-plan">
              {run.plan.rows.map((row) => (
                <li key={row.row_id}>
                  <code>
                    {row.warehouse_id} → {row.store_id}
                  </code>
                  <span>
                    {row.quantity} units / {row.truck_id}
                  </span>
                </li>
              ))}
            </ol>
            {run.plan.handoffs.length > 0 && (
              <div className="handoff-flow" aria-label="役割間の成果物受け渡し">
                {run.plan.handoffs.map((handoff) => (
                  <span key={`${handoff.from_role}-${handoff.to_role}`}>{handoff.artifact}</span>
                ))}
              </div>
            )}
            <ul className="event-list">
              {run.events.map((event) => (
                <li key={event.event_id} className={event.status}>
                  <strong>{event.status}</strong> {reasonLabels[event.reason]}
                  <small>
                    run {event.run_id.slice(0, 8)} · decision {event.decision_id.slice(0, 8)} ·
                    action {event.action_id.slice(0, 8)} · event {event.event_id.slice(0, 8)} · rev{" "}
                    {event.world_revision}
                  </small>
                </li>
              ))}
            </ul>
            <footer>
              結果artifact: 確定 {run.actual} / 未充足 {run.unfulfilled} / final rev{" "}
              {run.finalRevision}
            </footer>
          </article>
        ))}
      </div>

      <p className="sandbox-boundary">
        principal IDはローカル実験用。roleはserver
        registryで割り当て、Actorの自己申告roleは使いません。利用者の実認証は未実装です。通信結果不明はWorld
        failureと分け、自動再送しません。
      </p>
    </section>
  );
}
