import assert from "node:assert/strict";
import test from "node:test";

import { elapsed, sourceDescription } from "../../docs/status/status.js";

test("PM確認snapshotをCodex liveと表示しない", () => {
  // 手動報告をruntime eventへ格上げして見せる回帰を防ぐ。
  const [label, note] = sourceDescription("pm-confirmed");
  assert.equal(label, "PM確認snapshot");
  assert.match(note, /未接続/);
});

test("fixtureを実際のagent稼働と表示しない", () => {
  const [, note] = sourceDescription("fixture");
  assert.match(note, /実際のagent稼働を示しません/);
});

test("local event記録をApp Server live接続と表示しない", () => {
  const [label, note] = sourceDescription("local-event-record");
  assert.equal(label, "local event記録");
  assert.match(note, /live接続ではありません/);
});

test("経過時間を未来日時でも負数にしない", () => {
  assert.equal(elapsed("2026-09-06T00:01:00Z", Date.parse("2026-09-06T00:00:00Z")), "0秒前");
});
