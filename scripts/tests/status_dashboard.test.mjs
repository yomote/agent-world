import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  activityDescription,
  buildTree,
  capacityDescription,
  capacityMetrics,
  elapsed,
  parentDescription,
  parentSourceDescription,
  relationshipEdges,
  sessionTreeDescription,
  selectedAgentAfterRefresh,
  sourceDescription,
  statusDescription,
  summarizeItems,
  treeLayout,
} from "../../docs/status/status.js";

test("親子関係は明示値だけを使いroot・未知・snapshot外を区別する", () => {
  // role名から親を推測したり、欠損parentでrenderを止める回帰を防ぐ。
  const items = [
    { agent: "root", owner_label: "窓口", parent_relation: "root" },
    { agent: "child", owner_label: "実装", parent_relation: "delegated", parent_agent: "root" },
    { agent: "outside-child", parent_relation: "delegated", parent_agent: "outside" },
    { agent: "unknown", parent_relation: "unknown" },
  ];
  assert.equal(parentDescription(items[0], items), "依頼元なし（本作業の窓口）");
  assert.equal(parentDescription(items[1], items), "窓口");
  assert.equal(parentDescription(items[2], items), "outside（snapshot外）");
  assert.equal(parentDescription(items[3], items), "依頼元未取得");
  assert.equal(relationshipEdges(items).length, 4);
  assert.equal(
    parentSourceDescription("runtime-canonical-task-path"),
    "runtime canonical path観測",
  );
  assert.equal(parentSourceDescription("explicit-delegation"), "明示された委任");
  assert.equal(parentSourceDescription(undefined), "未取得");
});

test("runtime capacityはtask件数と別に実行中と上限を表示する", () => {
  // taskカードの件数を実行中agent数へ混ぜる回帰を防ぐ。
  assert.equal(
    capacityDescription({
      scope: "/root session tree",
      observed_at: "2026-09-12T16:55:23.9476494Z",
      state_source: "runtime-list-agents-metadata",
      limit_source: "runtime-instructions",
      running: 4,
      idle: 0,
      completed: 2,
      total: 6,
      max_concurrent_agents: 8,
    }).headline,
    "このセッション：観測時点の実行中 4 / 同時実行上限 8",
  );
});

test("maxだけのcapacityで実行中を推測しない", () => {
  // max-runningを空き枠や実行中として捏造する回帰を防ぐ。
  const description = capacityDescription({
    scope: "/root session tree",
    observed_at: "2026-09-12T16:55:23.9476494Z",
    limit_source: "runtime-instructions",
    max_concurrent_agents: 8,
  });
  assert.equal(description.headline, "このセッション：観測時点の実行中 未取得 / 同時実行上限 8");
  assert.equal(description.rows.find(([name]) => name === "実行状態の出所")[1], "未取得");
  assert.equal(description.metrics[2].value, "未取得");
});

test("capacity未取得と古いcapacity観測を区別する", () => {
  // snapshotの再受信をcapacity観測の更新へ見せる回帰を防ぐ。
  assert.match(capacityDescription(null).headline, /未取得/);
  const oldCapacity = capacityDescription({
    scope: "/root session tree",
    observed_at: "2020-01-01T00:00:00Z",
    state_source: "runtime-list-agents-metadata",
    running: 4,
  });
  assert.match(oldCapacity.headline, /観測時点/);
  assert.match(oldCapacity.rows.find(([name]) => name === "容量観測")[1], /日前/);
});

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
  assert.match(sourceDescription("fixture")[1], /実際のagent稼働を示しません/);
});

test("local event記録をApp Server live接続と表示しない", () => {
  const [label, note] = sourceDescription("local-event-record");
  assert.equal(label, "local event記録");
  assert.match(note, /live接続ではありません/);
});

test("保持付き部分更新を全行の自動取得と表示しない", () => {
  // 保持した手動rowまで新しいruntime eventから取得したように見せる回帰を防ぐ。
  const [label, note] = sourceDescription("ingest-upsert");
  assert.equal(label, "保持付き部分更新");
  assert.match(note, /指定項目だけ/);
});

test("経過時間を未来日時でも負数にしない", () => {
  assert.equal(elapsed("2026-09-06T00:01:00Z", Date.parse("2026-09-06T00:00:00Z")), "0秒前");
});

test("task状態を運用上の違いが分かる表示へ変換する", () => {
  // review待ち、人待ち、停止、完了、旧idleを同じ表示へ潰す回帰を防ぐ。
  assert.equal(statusDescription("not-started"), "未着手");
  assert.equal(statusDescription("review-wait"), "レビュー待ち");
  assert.equal(statusDescription("human-wait"), "人の判断待ち");
  assert.equal(statusDescription("stopped"), "停止済み");
  assert.equal(statusDescription("completed"), "完了");
  assert.equal(statusDescription("unknown"), "状態不明");
  assert.equal(statusDescription("idle"), "turn終了");
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

test("current全nodeを保持し履歴はcurrent優先で重複排除する", () => {
  // 履歴をcurrent人数へ加えたり、public rowのないinventory nodeを落とす回帰を防ぐ。
  const nodes = buildTree(
    {
      root_agent: "front",
      nodes: [
        { agent: "front", parent_agent: null },
        { agent: "worker", parent_agent: "front" },
      ],
    },
    {
      entries: [
        { agent: "worker", parent_agent: "front", status: "completed" },
        { agent: "old", parent_agent: "front", status: "completed" },
        { agent: "old", parent_agent: "front", status: "completed" },
      ],
    },
    [{ agent: "front", owner_label: "窓口" }],
  );
  assert.deepEqual(
    nodes.map(({ agent, kind }) => [agent, kind]),
    [
      ["front", "current"],
      ["worker", "current"],
      ["old", "history"],
    ],
  );
});

test("循環した履歴やdetached履歴でもlayoutは有限で全nodeを返す", () => {
  // 壊れた保存値でUIが無限再帰したりdetached履歴が消える回帰を防ぐ。
  const nodes = [
    { agent: "root", parent_agent: null },
    { agent: "a", parent_agent: "b" },
    { agent: "b", parent_agent: "a" },
    { agent: "detached", parent_agent: "outside" },
  ];
  assert.deepEqual(
    treeLayout(nodes, "root").map((node) => node.agent),
    ["root", "a", "b", "detached"],
  );
});

test("treeはscope・正規source・clockが揃った場合だけcurrent観測として扱う", () => {
  // sourceや観測時刻欠損をcurrent treeへ格上げする回帰を防ぐ。
  const valid = {
    scope: "/root session tree",
    observed_at: "2026-09-13T01:00:00Z",
    source: "runtime-list-agents-metadata",
    root_agent: "root",
    nodes: [{ agent: "root", parent_agent: null }],
    covered_agents: ["root"],
  };
  assert.equal(sessionTreeDescription(valid).valid, true);
  assert.equal(sessionTreeDescription({ ...valid, source: null }).valid, false);
  assert.equal(sessionTreeDescription({ ...valid, observed_at: null }).valid, false);
  assert.equal(sessionTreeDescription({ ...valid, scope: "" }).valid, false);
});

test("availableは値・source・definitionの明示が揃った場合だけ表示する", () => {
  // nullや由来なしの値を取得済み・最新の空きとして見せる回帰を防ぐ。
  const base = {
    scope: "/root session tree",
    observed_at: "2026-09-13T01:00:00Z",
    state_source: "runtime-list-agents-metadata",
    limit_source: "runtime-instructions",
    running: 4,
    max_concurrent_agents: 8,
  };
  assert.equal(capacityMetrics({ ...base, available: null })[2].value, "未取得");
  assert.equal(capacityMetrics({ ...base, available: 4 })[2].value, "未取得");
  assert.equal(
    capacityMetrics({
      ...base,
      available: 4,
      availability_source: "derived-running-limit",
      availability_definition: "max-concurrent-minus-running",
    })[2].value,
    4,
  );
});

test("旧refresh・stale・focus・時計・linkとkeyboard操作を実装に保持する", async () => {
  // tree置換時に利用者が確認していた表示意味と操作を消す回帰を防ぐ。
  const source = await readFile(new URL("../../docs/status/status.js", import.meta.url), "utf8");
  for (const fragment of [
    'notice.dataset.state = snapshot.stale || hasStaleItem ? "stale" : "live"',
    'document.visibilityState === "visible"',
    "10_000",
    "focus-source",
    "latest_activity_at",
    "parent_observed_at",
    "summary_updated_at",
    "issue_url",
    "pr_url",
    'event.key === "Enter"',
    'event.key === " "',
  ]) {
    assert.match(source, new RegExp(fragment.replace(/[.*+?^$()|[\]\\]/g, "\\$&")));
  }
});

test("asset queryはJS/CSSを同じv5へ更新し図だけpan可能にする", async () => {
  // 旧cacheの片方だけが残ることと390px page overflowの再発を防ぐ。
  const html = await readFile(new URL("../../docs/status/index.html", import.meta.url), "utf8");
  const css = await readFile(new URL("../../docs/status/status.css", import.meta.url), "utf8");
  assert.match(html, /status\.css\?v=5/);
  assert.match(html, /status\.js\?v=5/);
  assert.match(css, /\.tree-scroll\s*{[^}]*overflow:\s*auto/s);
  assert.match(css, /width:\s*calc\(100vw - 24px\)/);
});

test("公開task件数と状態内訳をtree node・履歴・capacityから分離して描画する", async () => {
  // 集計helperだけが残り、利用者からtask行数と内訳が消える回帰を防ぐ。
  const html = await readFile(new URL("../../docs/status/index.html", import.meta.url), "utf8");
  const source = await readFile(new URL("../../docs/status/status.js", import.meta.url), "utf8");
  assert.match(html, /id="task-total"[^>]*>公開タスク行/);
  assert.match(html, /id="status-counts"/);
  assert.match(source, /summarizeItems\(snapshot\.items\)/);
  assert.match(source, /公開タスク行.*current node・履歴とは別/);
});

test("容量metricはruntime turn観測でtask数・進捗・実作業人数と別だと明示する", () => {
  // 「稼働」を人やタスクの実稼働と誤読させる回帰を防ぐ。
  const description = capacityDescription({
    scope: "/root session tree",
    observed_at: "2026-09-13T01:00:00Z",
    state_source: "runtime-list-agents-metadata",
    running: 4,
  });
  assert.equal(description.metrics[0].label, "稼働（観測時点の実行中）");
  assert.match(description.note, /runtime turn状態/);
  assert.match(description.note, /タスク件数・進捗・実作業人数とは別/);
  assert.match(description.note, /起動できることは保証しません/);
});

test("refresh後は同じagentの選択を復元し、消えたagentだけ先頭へ戻す", () => {
  // 10秒refreshのSVG再生成で選択対象とkeyboard focusの復元先を失う回帰を防ぐ。
  const nodes = [{ agent: "front" }, { agent: "worker" }];
  assert.equal(selectedAgentAfterRefresh(nodes, "worker"), "worker");
  assert.equal(selectedAgentAfterRefresh(nodes, "completed-worker"), "front");
  assert.equal(selectedAgentAfterRefresh([], "worker"), null);
});
