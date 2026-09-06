import { describe, expect, it, vi } from "vitest";
import type { Action, ActionResult, WorldState } from "./api/types";
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
function result(command: Action, success = true): ActionResult {
  const world = initial();
  if (success) {
    world.revision = 1;
    world.entities[0].position.x = 4;
  }
  return {
    world,
    event: {
      event_id: crypto.randomUUID(),
      action: command,
      status: success ? "success" : "failure",
      reason: success ? "moved" : "out_of_bounds",
      before: { x: 3, y: 2 },
      after: world.entities[0].position,
      world_id: world.world_id,
      world_revision: world.revision,
    },
  };
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
    const session = new SandboxSession({ observe: async () => initial(), act });
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
      observe: async () => initial(),
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
    const session = new SandboxSession({ observe: async () => initial(), act });
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
    const observation = deferred<WorldState>();
    const observe = vi
      .fn()
      .mockResolvedValueOnce(initial())
      .mockImplementationOnce(() => observation.promise)
      .mockResolvedValue(initial());
    const act = vi.fn(async (command) => result(command));
    const session = new SandboxSession({ observe, act });
    await session.refresh();
    const refresh = session.refresh();
    const pending = session.act(action());
    expect(act).not.toHaveBeenCalled();
    observation.resolve(initial());
    await refresh;
    await pending;
    await session.refresh();
    expect(session.getSnapshot().world?.revision).toBe(1);
  });

  // サーバー再起動でrevisionが0に戻った際に、旧世界へ固定される回帰を防ぐ。
  it("world_idが変われば新しいWorldを採用し、旧Traceを分ける", async () => {
    const observe = vi
      .fn()
      .mockResolvedValueOnce(initial())
      .mockResolvedValue({ ...initial(), world_id: crypto.randomUUID() });
    const session = new SandboxSession({ observe, act: async (command) => result(command) });
    await session.refresh();
    await session.act(action());
    await session.refresh();
    expect(session.getSnapshot().world?.revision).toBe(0);
    expect(session.getSnapshot().trace).toEqual([]);
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
