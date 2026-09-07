import { describe, expect, it, vi } from "vitest";
import { handleMoveKeyDown } from "./moveKeyboard";

type MoveKeyEvent = Parameters<typeof handleMoveKeyDown>[0];

function keyEvent(overrides: Partial<MoveKeyEvent> = {}): MoveKeyEvent {
  const panel = new EventTarget() as HTMLElement;
  return {
    key: "ArrowRight",
    target: panel,
    currentTarget: panel,
    defaultPrevented: false,
    nativeEvent: { isComposing: false },
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    repeat: false,
    preventDefault: vi.fn(),
    ...overrides,
  };
}

describe("World領域の矢印キー操作", () => {
  // 画面座標の上下反転や、長押しによる連続Actionとページスクロールを防ぐ。
  it.each([
    ["ArrowUp", 0, -1],
    ["ArrowDown", 0, 1],
    ["ArrowLeft", -1, 0],
    ["ArrowRight", 1, 0],
  ] as const)("%sは押し始めだけmove(%i, %i)を発行する", (key, dx, dy) => {
    const move = vi.fn();
    const event = keyEvent({ key });
    handleMoveKeyDown(event, true, move);
    handleMoveKeyDown({ ...event, repeat: true }, true, move);
    handleMoveKeyDown({ ...event, repeat: true }, true, move);
    expect(move.mock.calls).toEqual([[dx, dy]]);
    expect(event.preventDefault).toHaveBeenCalledTimes(3);
    handleMoveKeyDown(event, true, move);
    expect(move.mock.calls).toEqual([
      [dx, dy],
      [dx, dy],
    ]);
  });

  // ボタンが利用不可の間にキーボードだけがActionを発行する回帰を防ぐ。
  it("手動操作が無効なら発行も既定操作の抑止もしない", () => {
    const event = keyEvent();
    const move = vi.fn();
    handleMoveKeyDown(event, false, move);
    expect(move).not.toHaveBeenCalled();
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  // 子のinput/textarea/select/contenteditableから届く入力を領域が奪う回帰を防ぐ。
  it("フォーカスが領域自身にないイベントは処理しない", () => {
    const event = keyEvent({ target: new EventTarget() });
    const move = vi.fn();
    handleMoveKeyDown(event, true, move);
    expect(move).not.toHaveBeenCalled();
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  // ブラウザ・OSのショートカット、IME、他のキー処理を妨げる回帰を防ぐ。
  it.each<Partial<MoveKeyEvent>>([
    { altKey: true },
    { ctrlKey: true },
    { metaKey: true },
    { shiftKey: true },
    { nativeEvent: { isComposing: true } },
    { defaultPrevented: true },
    { key: "Enter" },
    { key: "Tab" },
  ])("対象外の操作 %j を処理しない", (overrides) => {
    const event = keyEvent(overrides);
    const move = vi.fn();
    handleMoveKeyDown(event, true, move);
    expect(move).not.toHaveBeenCalled();
    expect(event.preventDefault).not.toHaveBeenCalled();
  });
});
