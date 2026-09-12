import { describe, expect, it, vi } from "vitest";
import type { Action, ActionResult, EventHistory, WorldEvent, WorldState } from "./api/types";
import { createRandomActor } from "./actors/randomActor";
import { SandboxSession } from "./session";

const initial = (): WorldState => ({
  world_id: "36e9fc42-804f-4a9d-b22c-ae919090d5ea",
  revision: 0,
  width: 8,
  height: 6,
  entities: [{ id: "A", position: { x: 3, y: 2 } }],
});
const action = (): Action => ({
  action_id: crypto.randomUUID(),
  actor_id: "A",
  type: "move",
  dx: 1,
  dy: 0,
});
const history = (world = initial(), events: WorldEvent[] = []): EventHistory => ({ world, events });
function result(command: Action, success = true): ActionResult {
  const world = initial();
  if (success) {
    world.revision = 1;
    world.entities[0].position.x = 4;
  }
  const event: WorldEvent = {
    event_id: crypto.randomUUID(),
    action: command,
    status: success ? "success" : "failure",
    reason: success ? "moved" : "out_of_bounds",
    before: { x: 3, y: 2 },
    after: world.entities[0].position,
    world_id: world.world_id,
    world_revision: world.revision,
  };
  return { world, event, events: [event] };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("Worldと表示の境界", () => {
  // 応答前の座標変更と、1回のクリックから複数Actionが出る回帰を防ぐ。
  it("確定応答まで位置を変えず、重複操作を送信しない", async () => {
    const response = deferred<ActionResult>();
    const act = vi.fn(() => response.promise);
    const session = new SandboxSession({ observe: async () => history(), act });
    await session.refresh();
    const command = action();
    const pending = session.act(command);
    expect(session.getSnapshot().world).toEqual(initial());
    expect(session.getSnapshot().trace).toEqual([]);
    expect(await session.act(action())).toBe(false);
    response.resolve(result(command));
    await pending;
    expect(act).toHaveBeenCalledTimes(1);
    expect(session.getSnapshot().world?.entities[0].position).toEqual({ x: 4, y: 2 });
    expect(session.getSnapshot().trace[0]).toMatchObject({
      kind: "event",
      event: { status: "success", action: command },
    });
  });

  // failureのEventを受けても画面内で独自に移動してしまう回帰を防ぐ。
  it("failureは受け取った状態を保ち、Traceへ残す", async () => {
    const session = new SandboxSession({
      observe: async () => history(),
      act: async (command) => result(command, false),
    });
    await session.refresh();
    await session.act(action());
    expect(session.getSnapshot().world).toEqual(initial());
    expect(session.getSnapshot().trace[0]).toMatchObject({
      kind: "event",
      event: { status: "failure" },
    });
  });

  // ネットワーク断をWorldの拒否・成功に偽装し、二重実行する回帰を防ぐ。
  it("通信失敗は結果不明として記録し、自動再送しない", async () => {
    const act = vi.fn(async () => {
      throw new Error("network disconnected");
    });
    const session = new SandboxSession({ observe: async () => history(), act });
    await session.refresh();
    expect(await session.act(action())).toBe(false);
    expect(session.getSnapshot().trace[0]).toMatchObject({ kind: "unknown" });
    expect(session.getSnapshot().world).toEqual(initial());
    expect(session.getSnapshot().connection).toBe("offline");
    await session.refresh();
    expect(act).toHaveBeenCalledTimes(1);
    expect(session.getSnapshot().trace).toHaveLength(1);
  });

  // 古いGETの応答が成功したmoveの後に来て座標を巻き戻す回帰を防ぐ。
  it("GETが完了してからPOSTし、古いrevisionも採用しない", async () => {
    const observation = deferred<EventHistory>();
    const observe = vi
      .fn()
      .mockResolvedValueOnce(history())
      .mockImplementationOnce(() => observation.promise)
      .mockResolvedValue(history());
    const act = vi.fn(async (command) => result(command));
    const session = new SandboxSession({ observe, act });
    await session.refresh();
    const refresh = session.refresh();
    const pending = session.act(action());
    expect(act).not.toHaveBeenCalled();
    observation.resolve(history());
    await refresh;
    await pending;
    await session.refresh();
    expect(session.getSnapshot().world?.revision).toBe(1);
  });

  // サーバー再起動でrevisionが0に戻った際に、旧世界へ固定される回帰を防ぐ。
  it("world_idが変われば新しいWorldを採用し、旧Traceを分ける", async () => {
    const observe = vi
      .fn()
      .mockResolvedValueOnce(history())
      .mockResolvedValue(history({ ...initial(), world_id: crypto.randomUUID() }));
    const session = new SandboxSession({ observe, act: async (command) => result(command) });
    await session.refresh();
    await session.act(action());
    await session.refresh();
    expect(session.getSnapshot().world?.revision).toBe(0);
    expect(session.getSnapshot().trace).toEqual([]);
  });
});

describe("共有Worldの有限Trace", () => {
  // タブ間のStateだけの同期、POST/pollの二重表示、同revisionのfailure消失を防ぐ。
  it("別sessionの成功とfailureを取得し、POSTと重なっても一度ずつ新しい順に表示する", async () => {
    let shared = history();
    const api = {
      observe: async () => shared,
      act: vi.fn(async (command: Action) => {
        const response = result(command, shared.world.revision === 0);
        if (shared.world.revision > 0) {
          response.world = shared.world;
          response.event.before = shared.world.entities[0].position;
          response.event.after = shared.world.entities[0].position;
        }
        response.event.world_revision = 1;
        response.events = [...shared.events, response.event];
        shared = response;
        return response;
      }),
    };
    const first = new SandboxSession(api);
    const second = new SandboxSession(api);
    await Promise.all([first.refresh(), second.refresh()]);
    await first.act(action());
    // 2タブ目のPOST応答には、その直前に別タブで確定したEventも含む。
    await second.act(action());
    await Promise.all([first.refresh(), second.refresh()]);
    await first.refresh();
    expect(api.act).toHaveBeenCalledTimes(2);
    const expected = [...shared.events].reverse().map((event) => ({ kind: "event", event }));
    expect(first.getSnapshot().trace).toEqual(expected);
    expect(second.getSnapshot().trace).toEqual(expected);
    expect(expected.map((entry) => entry.event.status)).toEqual(["failure", "success"]);
    expect(first.getSnapshot().world).toEqual(shared.world);
    const newlyOpened = new SandboxSession(api);
    await newlyOpened.refresh();
    expect(newlyOpened.getSnapshot().trace).toEqual(expected);
  });

  // 履歴のロールオーバーと通信失敗の追加で表示外になったIDをpollで復活させない。
  it("80件を超えず、結果不明を保持した再取得でも古いEventを再追加しない", async () => {
    const events = Array.from({ length: 80 }, () => result(action(), false).event);
    let shared = history(initial(), events);
    const act = vi.fn(async () => {
      throw new Error("response lost");
    });
    const session = new SandboxSession({ observe: async () => shared, act });
    await session.refresh();
    expect(session.getSnapshot().trace).toHaveLength(80);
    await session.act(action());
    const withUnknown = session.getSnapshot().trace;
    await session.refresh();
    await session.refresh();
    expect(session.getSnapshot().trace).toEqual(withUnknown);
    expect(session.getSnapshot().trace[0].kind).toBe("unknown");
    const newest = result(action(), false).event;
    shared = history(initial(), [...events.slice(1), newest]);
    await session.refresh();
    expect(session.getSnapshot().trace).toHaveLength(80);
    expect(session.getSnapshot().trace[0]).toEqual({ kind: "event", event: newest });
    expect(session.getSnapshot().trace[1].kind).toBe("unknown");
    expect(act).toHaveBeenCalledTimes(1);
  });

  // 再起動後に旧WorldのEvent/結果不明/既読IDが残る回帰を防ぐ。
  it("world_id切替時は同じevent_idでも新Worldの履歴へ置き換え、混入を防ぐ", async () => {
    const old = result(action());
    let shared: EventHistory = old;
    const session = new SandboxSession({
      observe: async () => shared,
      act: async () => {
        throw new Error("network");
      },
    });
    await session.refresh();
    await session.act(action());
    const world = { ...initial(), world_id: crypto.randomUUID() };
    const fresh = {
      ...result(action(), false).event,
      event_id: old.event.event_id,
      world_id: world.world_id,
    };
    shared = history(world, [old.event, fresh]);
    await session.refresh();
    expect(session.getSnapshot().world).toEqual(world);
    expect(session.getSnapshot().trace).toEqual([{ kind: "event", event: fresh }]);
  });

  // GET応答待ちの重複pollと通信エラーが履歴を壊したりActionを再送する回帰を防ぐ。
  it("取得失敗は最後の確定履歴を保ち、再接続で新Eventを重複なく取り込む", async () => {
    const first = result(action(), false);
    const next = result(action(), false).event;
    const pending = deferred<EventHistory>();
    const observe = vi
      .fn()
      .mockResolvedValueOnce(first)
      .mockRejectedValueOnce(new Error("offline"))
      .mockImplementationOnce(() => pending.promise);
    const act = vi.fn(async (command: Action) => result(command));
    const session = new SandboxSession({ observe, act });
    await session.refresh();
    const trace = session.getSnapshot().trace;
    await session.refresh();
    expect(session.getSnapshot().connection).toBe("offline");
    expect(session.getSnapshot().trace).toEqual(trace);
    const refresh = session.refresh();
    expect(session.refresh()).toBe(refresh);
    pending.resolve(history(first.world, [first.event, first.event, next]));
    await refresh;
    expect(session.getSnapshot().connection).toBe("online");
    expect(session.getSnapshot().trace).toEqual([
      { kind: "event", event: next },
      { kind: "event", event: first.event },
    ]);
    expect(act).not.toHaveBeenCalled();
  });
});

describe("交換可能なActor", () => {
  // ActorへWorldの変更権を渡したり、境界外の提案を隠す回帰を防ぐ。
  it("境界でも隣接移動を提案するだけで観測は変更しない", () => {
    const observation = initial();
    observation.entities[0].position = Object.freeze({ x: 0, y: 0 });
    Object.freeze(observation.entities[0]);
    Object.freeze(observation.entities);
    Object.freeze(observation);
    const actions = [0, 0.25, 0.5, 0.75].map((random) =>
      createRandomActor(() => random).propose(observation),
    );
    expect(actions.map((command) => [command?.dx, command?.dy])).toEqual([
      [0, -1],
      [1, 0],
      [0, 1],
      [-1, 0],
    ]);
    expect(new Set(actions.map((command) => command?.action_id)).size).toBe(4);
    expect(observation.entities[0].position).toEqual({ x: 0, y: 0 });
  });
});
