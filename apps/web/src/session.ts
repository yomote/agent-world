import type { Action, ActionResult, EventHistory, WorldEvent, WorldState } from "./api/types";

export interface WorldApi {
  observe(): Promise<EventHistory>;
  act(action: Action): Promise<ActionResult>;
}

export type TraceEntry =
  { kind: "event"; event: WorldEvent } | { kind: "unknown"; action: Action; detail: string };

interface SessionState {
  world: WorldState | null;
  trace: readonly TraceEntry[];
  connection: "connecting" | "online" | "offline";
  busy: boolean;
  error: string | null;
}

// 状態変更の提案と確定を分離。GETとPOSTを直列化して古い観測で巻き戻さない。
export class SandboxSession {
  private state: SessionState = {
    world: null,
    trace: [],
    connection: "connecting",
    busy: false,
    error: null,
  };
  private listeners = new Set<() => void>();
  private observing: Promise<void> | null = null;
  // 表示から押し出されたEventも次のpollで再追加しない。保持は最新snapshotの最大80 IDだけ。
  private observedEventIds = new Set<string>();

  constructor(private readonly api: WorldApi) {}

  getSnapshot = () => this.state;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private update(patch: Partial<SessionState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  private accept({ world, events }: EventHistory) {
    const previous = this.state.world;
    if (previous?.world_id === world.world_id && previous.revision > world.revision) return;
    const reset = previous !== null && previous.world_id !== world.world_id;
    if (reset) this.observedEventIds.clear();
    const snapshot = new Map(
      events
        .filter((event) => event.world_id === world.world_id)
        .slice(-80)
        .map((event) => [event.event_id, event]),
    );
    const added = [...snapshot.values()]
      .filter((event) => !this.observedEventIds.has(event.event_id))
      .reverse()
      .map((event): TraceEntry => ({ kind: "event", event }));
    const retained = reset
      ? []
      : this.state.trace.filter(
          (entry) => entry.kind === "unknown" || snapshot.has(entry.event.event_id),
        );
    this.observedEventIds = new Set(snapshot.keys());
    this.update({
      world,
      trace: [...added, ...retained].slice(0, 80),
      connection: "online",
      error: null,
    });
  }

  refresh = (): Promise<void> => {
    if (this.state.busy) return Promise.resolve();
    if (this.observing) return this.observing;
    this.observing = this.api
      .observe()
      .then((history) => this.accept(history))
      .catch((error: unknown) => {
        this.update({ connection: "offline", error: `Worldを取得できません: ${String(error)}` });
      })
      .finally(() => {
        this.observing = null;
      });
    return this.observing;
  };

  act = async (action: Action): Promise<boolean> => {
    if (this.state.busy || !this.state.world || this.state.connection !== "online") return false;
    this.update({ busy: true });
    await this.observing;
    try {
      const result = await this.api.act(action);
      this.accept(result);
      return true;
    } catch (error) {
      const detail = `通信失敗・結果不明（自動再送なし）: ${String(error)}`;
      this.update({
        trace: [{ kind: "unknown" as const, action, detail }, ...this.state.trace].slice(0, 80),
        connection: "offline",
        error: detail,
      });
      return false;
    } finally {
      this.update({ busy: false });
    }
  };
}
