import assert from "node:assert/strict";
import test from "node:test";

import {
  activityDescription,
  elapsed,
  sourceDescription,
  statusDescription,
  summarizeItems,
} from "../../docs/status/status.js";

test("PM確認snapshotをCodex liveと表示しない", () => {
  // 手動報告をruntime eventへ格上げして見せる回帰を防ぐ。
  const [label, note] = sourceDescription("pm-confirmed");
  assert.equal(label, "PM確認snapshot");
  assert.match(note, /未接続/);
});

test("activity欠損を状態観測時刻から推測しない", () => {
  // 保存済み旧snapshotのobserved_atをactivity時刻へ格上げする回帰を防ぐ。
  assert.equal(
    activityDescription({ observed_at: "2026-09-06T12:00:00Z", latest_activity_at: null }),
    null,
  );
  assert.deepEqual(
    activityDescription({
      observed_at: "2026-09-06T12:00:00Z",
      latest_activity: "task-started",
      latest_activity_at: "2026-09-06T12:01:00Z",
    }),
    { label: "task開始", observedAt: "2026-09-06T12:01:00Z" },
  );
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

test("task状態を運用上の違いが分かる表示へ変換する", () => {
  // review待ち、人待ち、停止、完了を同じ停止表示へ潰す回帰を防ぐ。
  assert.equal(statusDescription("not-started"), "未着手");
  assert.equal(statusDescription("review-wait"), "レビュー待ち");
  assert.equal(statusDescription("human-wait"), "人の判断待ち");
  assert.equal(statusDescription("stopped"), "停止済み");
  assert.equal(statusDescription("completed"), "完了");
  assert.equal(statusDescription("unknown"), "状態不明");
});

test("表示件数を担当人数と誤認させずtask状態ごとに集計する", () => {
  // 同じ担当の複数taskを人数として数え、進捗を水増しして見せる回帰を防ぐ。
  assert.deepEqual(
    summarizeItems([
      { status: "running", owner_label: "担当A" },
      { status: "review-wait", owner_label: "担当A" },
      { status: "running", owner_label: "担当B" },
    ]),
    {
      total: 3,
      statuses: [
        { status: "running", count: 2 },
        { status: "review-wait", count: 1 },
      ],
    },
  );
});
