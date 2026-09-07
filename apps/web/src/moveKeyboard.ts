import type { KeyboardEvent } from "react";

const directions = new Map<string, readonly [number, number]>([
  ["ArrowUp", [0, -1]],
  ["ArrowDown", [0, 1]],
  ["ArrowLeft", [-1, 0]],
  ["ArrowRight", [1, 0]],
]);

type MoveKeyEvent = Pick<
  KeyboardEvent<HTMLElement>,
  | "key"
  | "target"
  | "currentTarget"
  | "defaultPrevented"
  | "altKey"
  | "ctrlKey"
  | "metaKey"
  | "shiftKey"
  | "repeat"
  | "preventDefault"
> & { nativeEvent: { isComposing: boolean } };

export function handleMoveKeyDown(
  event: MoveKeyEvent,
  enabled: boolean,
  move: (dx: number, dy: number) => void,
) {
  // 領域自身のフォーカスだけを扱い、子の入力欄や編集領域には干渉しない。
  if (
    !enabled ||
    event.target !== event.currentTarget ||
    event.defaultPrevented ||
    event.nativeEvent.isComposing ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey
  )
    return;

  const direction = directions.get(event.key);
  if (!direction) return;
  event.preventDefault();
  // 長押し中のスクロールは抑えるが、Actionは最初のkeydownだけで発行する。
  if (!event.repeat) move(direction[0], direction[1]);
}
