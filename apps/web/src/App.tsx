import { useEffect, useState, useSyncExternalStore } from "react";
import { createRandomActor } from "./actors/randomActor";
import { worldApi } from "./api/client";
import type { Action, WorldEvent } from "./api/types";
import { handleMoveKeyDown } from "./moveKeyboard";
import { SandboxSession, type TraceEntry } from "./session";
import { WorldCanvas } from "./world/WorldCanvas";

const reasons: Record<WorldEvent["reason"], string> = {
  moved: "移動を確定",
  out_of_bounds: "Worldの境界外",
  invalid_move: "上下左右の1マス移動のみ有効",
  unknown_actor: "存在しないActor",
};

function TraceRow({ entry }: { entry: TraceEntry }) {
  const event = entry.kind === "event" ? entry.event : null;
  const action = entry.kind === "event" ? entry.event.action : entry.action;
  const status = event?.status ?? "unknown";
  return (
    <li className={`trace-row ${status}`}>
      <div className="trace-flow">
        <strong>{action.actor_id}</strong>
        <span>→</span>
        <code>
          move(dx={action.dx}, dy={action.dy})
        </code>
        <span>→</span>
        <strong className={`result ${status}`}>{status}</strong>
      </div>
      <div className="trace-detail">
        {event ? (
          <>
            <span>{reasons[event.reason]}</span>
            {event.before && event.after && (
              <code>
                ({event.before.x}, {event.before.y}) → ({event.after.x}, {event.after.y})
              </code>
            )}
            <span>rev {event.world_revision}</span>
          </>
        ) : (
          <span>{entry.kind === "unknown" && entry.detail}</span>
        )}
        <span title={action.action_id}>action {action.action_id.slice(0, 8)}</span>
      </div>
    </li>
  );
}

export default function App() {
  const [session] = useState(() => new SandboxSession(worldApi));
  const [actor] = useState(() => createRandomActor());
  const [running, setRunning] = useState(false);
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { world, trace, connection, busy, error } = state;
  const entity = world?.entities.find((item) => item.id === "A");
  const available = Boolean(world && connection === "online" && !busy);

  useEffect(() => {
    void session.refresh();
    const interval = window.setInterval(() => void session.refresh(), 1000);
    return () => window.clearInterval(interval);
  }, [session]);

  useEffect(() => {
    if (!running || !available || !world) return;
    const timer = window.setTimeout(() => {
      const action = actor.propose(world);
      if (action)
        void session.act(action).then((received) => {
          if (!received) setRunning(false);
        });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [running, available, world, actor, session]);

  function move(dx: number, dy: number) {
    if (!available || running) return;
    const action: Action = { action_id: crypto.randomUUID(), actor_id: "A", type: "move", dx, dy };
    void session.act(action);
  }

  return (
    <main>
      <header className="topbar">
        <div>
          <p className="eyebrow">WORLD OBSERVATORY / 01</p>
          <h1>AI Agent Sandbox</h1>
        </div>
        <div className={`connection ${connection}`} role="status">
          <span className="status-dot" />
          {connection === "online"
            ? "World 接続中"
            : connection === "offline"
              ? "World 未接続"
              : "World 接続待ち"}
        </div>
      </header>

      {error && (
        <div className="error" role="alert">
          {error}。{world ? "最後に確認した状態を表示しています。" : "起動後に自動で再接続します。"}
          <button onClick={() => void session.refresh()}>再取得</button>
        </div>
      )}

      <div className="workspace">
        <section
          className="world-panel"
          aria-labelledby="world-title"
          aria-describedby="world-keyboard-hint"
          tabIndex={0}
          onKeyDown={(event) => handleMoveKeyDown(event, available && !running, move)}
        >
          <div className="panel-heading">
            <h2 id="world-title">World</h2>
            <span>
              {world ? `${world.width} × ${world.height} · rev ${world.revision}` : "接続待ち"}
            </span>
          </div>
          {world ? (
            <WorldCanvas world={world} />
          ) : (
            <div className="loading">SimulatorからWorldを取得しています…</div>
          )}
          <div className="world-footer">
            <span>● Agent A</span>
            <span>原点: 左上 / x → / y ↓</span>
          </div>
          <p className="world-footer" id="world-keyboard-hint">
            このWorld領域をクリック、またはTabで選択して矢印キーでmove。長押しでは連続発行しません。
          </p>
        </section>

        <aside className="actor-panel" aria-labelledby="actor-title">
          <p className="eyebrow">RESIDENT</p>
          <div className="actor-heading">
            <span className="avatar">A</span>
            <div>
              <h2 id="actor-title">Agent A</h2>
              <p>Manual / Random actor</p>
            </div>
          </div>
          <dl className="observation">
            <div>
              <dt>確定位置</dt>
              <dd>{entity ? `(${entity.position.x}, ${entity.position.y})` : "—"}</dd>
            </div>
            <div>
              <dt>World revision</dt>
              <dd>{world?.revision ?? "—"}</dd>
            </div>
          </dl>
          <p className="control-label">move を発行</p>
          <div className="direction-pad">
            <button
              className="up"
              aria-label="上に移動"
              disabled={!available || running}
              onClick={() => move(0, -1)}
            >
              ↑
            </button>
            <button
              className="left"
              aria-label="左に移動"
              disabled={!available || running}
              onClick={() => move(-1, 0)}
            >
              ←
            </button>
            <span className="pad-center">A</span>
            <button
              className="right"
              aria-label="右に移動"
              disabled={!available || running}
              onClick={() => move(1, 0)}
            >
              →
            </button>
            <button
              className="down"
              aria-label="下に移動"
              disabled={!available || running}
              onClick={() => move(0, 1)}
            >
              ↓
            </button>
          </div>
          <button
            className="random-button"
            aria-pressed={running}
            disabled={!running && !available}
            onClick={() => setRunning(!running)}
          >
            {running ? "■ Random actor を停止" : "▶ Random actor を開始"}
          </button>
          <p className="hint">
            約0.7秒ごとに上下左右を選択。端の外へのActionも発行し、Worldの判定を観察します。
          </p>
          <div className="dispatch-status" role="status">
            {busy ? "Action送信中…" : running ? "次のActionを待機中" : "手動操作を待機中"}
          </div>
        </aside>
      </div>

      <section className="trace-panel" aria-labelledby="trace-title">
        <div className="panel-heading">
          <h2 id="trace-title">Action Trace</h2>
          <span>共有Worldの履歴 {trace.length} / 80 件 · 結果不明はこのタブのみ · 新しい順</span>
        </div>
        <div className="causal-path">
          <span>Agent</span>
          <span>→</span>
          <span>Action</span>
          <span>→</span>
          <span>World Simulator</span>
          <span>→</span>
          <span>Event + World Change</span>
        </div>
        {trace.length ? (
          <ol className="trace-list" aria-label="Actionの実行履歴">
            {trace.map((entry) => (
              <TraceRow
                key={entry.kind === "event" ? entry.event.event_id : entry.action.action_id}
                entry={entry}
              />
            ))}
          </ol>
        ) : (
          <p className="empty-trace">
            まだActionはありません。矢印ボタンかRandom actorでAを動かしてください。
          </p>
        )}
      </section>
      <footer className="page-footer">
        <span>LLMなし · メモリ内World · 再起動でリセット</span>
        <span>World Simulator is authoritative.</span>
      </footer>
    </main>
  );
}
