import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  activeRuntimeSnapshot,
  activityDescription,
  buildTree,
  capacityDescription,
  capacityMetrics,
  elapsed,
  parentDescription,
  parentSourceDescription,
  orderedRequestsForBoard,
  relationshipEdges,
  pmTaskBoardStatus,
  requestConnectionDescription,
  requestBoardStatus,
  requestFocusDescription,
  requestHasCurrentConnection,
  requestLifecycleDescription,
  requestScopedSnapshot,
  sessionTreeDescription,
  selectedAgentAfterRefresh,
  selectedRequestAfterRefresh,
  sourceDescription,
  statusDescription,
  summarizeItems,
  treePanDistance,
  treePanState,
  treeLayout,
  runtimeBindingMatchesActiveFrontDesk,
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
    "notice.dataset.state = snapshot.stale || hasStaleItem || bindingMismatch",
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

test("asset queryはJS/CSSを同じv9へ更新し図だけpan可能にする", async () => {
  // 旧cacheの片方だけが残ることと390px page overflowの再発を防ぐ。
  const html = await readFile(new URL("../../docs/status/index.html", import.meta.url), "utf8");
  const css = await readFile(new URL("../../docs/status/status.css", import.meta.url), "utf8");
  assert.match(html, /status\.css\?v=9/);
  assert.match(html, /status\.js\?v=9/);
  assert.match(css, /\.tree-scroll\s*{[^}]*overflow:\s*auto/s);
  assert.match(css, /width:\s*calc\(100vw - 24px\)/);
});

test("公開task件数と状態内訳をtree node・履歴・capacityから分離して描画する", async () => {
  // 集計helperだけが残り、利用者からtask行数と内訳が消える回帰を防ぐ。
  const html = await readFile(new URL("../../docs/status/index.html", import.meta.url), "utf8");
  const source = await readFile(new URL("../../docs/status/status.js", import.meta.url), "utf8");
  assert.match(html, /id="task-total"[^>]*>公開タスク行/);
  assert.match(html, /id="status-counts"/);
  assert.match(source, /summarizeItems\(scoped\.items\)/);
  assert.match(source, /公開(?:タスク行|担当行).*current node・履歴とは別/);
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

test("refreshでfocused agentが消えた場合も先頭fallbackへfocusする", async () => {
  // focused node消失時にbodyへfocusが落ちてkeyboard操作を失う回帰を防ぐ。
  const source = await readFile(new URL("../../docs/status/status.js", import.meta.url), "utf8");
  assert.match(source, /\(restored \|\| selectedGroup\)\?\.focus\(\{ preventScroll: true \}\)/);
});

test("tree panは左右方向と両端のdisabledをscroll寸法だけで決める", () => {
  // 横長treeを動かせず、端でも無効状態が更新されない回帰を防ぐ。
  assert.deepEqual(treePanState(0, 366, 900), {
    leftDisabled: true,
    rightDisabled: false,
  });
  assert.deepEqual(treePanState(534, 366, 900), {
    leftDisabled: false,
    rightDisabled: true,
  });
  assert.equal(treePanDistance(366, -1), -256);
  assert.equal(treePanDistance(366, 1), 256);
});

test("tree pan controlsを同じscroll containerへ接続する", async () => {
  // mouseとnative button keyboard操作の正規UI経路が配線から消える回帰を防ぐ。
  const html = await readFile(new URL("../../docs/status/index.html", import.meta.url), "utf8");
  const source = await readFile(new URL("../../docs/status/status.js", import.meta.url), "utf8");
  assert.match(html, /id="tree-pan-left"[^>]*type="button"[^>]*aria-label="ツリー図を左へ移動"/s);
  assert.match(html, /id="tree-pan-right"[^>]*type="button"[^>]*aria-label="ツリー図を右へ移動"/s);
  assert.match(source, /#tree-pan-left"\)\.addEventListener\("click", \(\) => panTree\(-1\)\)/);
  assert.match(source, /#tree-pan-right"\)\.addEventListener\("click", \(\) => panTree\(1\)\)/);
  assert.match(source, /#tree-scroll"\)\.addEventListener\("scroll", updateTreePanControls\)/);
});

const registrySnapshot = {
  items: [
    { agent: "front-desk", status: "running" },
    { agent: "status-owner", status: "running" },
    { agent: "other-request", status: "running" },
  ],
  session_tree: {
    scope: "root session tree",
    observed_at: "2026-09-13T04:00:00Z",
    source: "runtime-list-agents-metadata",
    root_agent: "front-desk",
    nodes: [
      { agent: "front-desk", parent_agent: null },
      { agent: "status-owner", parent_agent: "front-desk" },
      { agent: "other-request", parent_agent: "front-desk" },
    ],
    covered_agents: ["front-desk", "status-owner", "other-request"],
  },
  known_history: {
    recorded_at: "2026-09-13T04:00:00Z",
    entries: [
      {
        agent: "status-owner",
        parent_agent: "front-desk",
        status: "completed",
        source: "pm-recorded-completed-work-unit",
      },
      {
        agent: "old-worker",
        parent_agent: "front-desk",
        status: "completed",
        source: "pm-recorded-completed-work-unit",
      },
    ],
  },
};

const request59 = {
  request_id: "request-59",
  scope_id: "scope-request-59",
  lifecycle: "completed",
  member_agents: ["front-desk", "status-owner"],
  report_updated_at: "2026-09-13T03:45:00Z",
  runtime_connection: "record-only",
  runtime_observed_at: "2026-09-13T03:45:00Z",
  public_purpose: "担当関係を確認する",
  progress_summary: "PR 61を公開済み",
  next_action: null,
  blocker: null,
  report_source: "manual-public-summary",
};

const request64 = {
  request_id: "request-64",
  scope_id: "scope-request-64",
  lifecycle: "running",
  member_agents: ["front-desk", "status-owner", "old-worker"],
  report_updated_at: "2026-09-13T04:00:00Z",
  runtime_connection: "connected",
  runtime_observed_at: "2026-09-13T04:00:00Z",
  public_purpose: "新しい窓口へ未完依頼を引き継ぐ",
  progress_summary: "APIとCLIを検証中",
  next_action: "独立review",
  blocker: null,
  report_source: "manual-public-summary",
};

test("default requestは未完の最新報告、全完了なら先頭、選択済みなら維持する", () => {
  // 配列順や完了済み依頼へ意図せず選択が飛ぶ回帰を防ぐ。
  const registry = { requests: [request59, request64] };
  assert.equal(selectedRequestAfterRefresh(registry, null), "request-64");
  assert.equal(selectedRequestAfterRefresh(registry, "request-59"), "request-59");
  assert.equal(
    selectedRequestAfterRefresh(
      { requests: [request59, { ...request64, lifecycle: "completed" }] },
      null,
    ),
    "request-59",
  );
});

test("PM boardは選択中・未完・新しい報告の順で現在taskを先に見せる", () => {
  // 完了済みの長いcardが現在taskを画面外へ押し出す回帰を防ぐ。
  const older = {
    ...request64,
    request_id: "request-older",
    report_updated_at: "2026-09-13T03:00:00Z",
  };
  assert.deepEqual(
    orderedRequestsForBoard([request59, older, request64], "request-64").map(
      (request) => request.request_id,
    ),
    ["request-64", "request-older", "request-59"],
  );
});

test("connected requestだけmember aliasのcurrentと重複しない履歴へscopeする", () => {
  // 別request行を混ぜたり、同aliasのcurrentとhistoryを二重表示する回帰を防ぐ。
  const snapshot = {
    ...registrySnapshot,
    request_registry: {
      active_front_desk: { claimed_at: "2026-09-13T04:00:00Z" },
      requests: [request59, request64],
    },
  };
  const scoped = requestScopedSnapshot(snapshot, request64);
  assert.deepEqual(
    scoped.session_tree.nodes.map((node) => node.agent),
    ["front-desk", "status-owner"],
  );
  assert.deepEqual(
    scoped.known_history.entries.map((entry) => entry.agent),
    ["old-worker"],
  );
  assert.deepEqual(
    scoped.items.map((item) => item.agent),
    ["front-desk", "status-owner"],
  );
  assert.equal(scoped.request_context.connection_confirmed, true);
});

test("record-only requestは同aliasがcurrent treeにあっても最新treeと履歴を適用しない", () => {
  // 完了59と進行64の共有aliasから、過去依頼を現在稼働へ見せる回帰を防ぐ。
  const snapshot = {
    ...registrySnapshot,
    request_registry: { requests: [request59, request64] },
  };
  const scoped = requestScopedSnapshot(snapshot, request59);
  assert.equal(scoped.session_tree, null);
  assert.equal(scoped.known_history, null);
  assert.deepEqual(scoped.items, []);
  assert.equal(scoped.request_context.connection_confirmed, false);
  assert.match(
    requestConnectionDescription(
      request59,
      new Set(["front-desk", "status-owner"]),
      snapshot.request_registry,
      snapshot.session_tree.observed_at,
    ),
    /記録のみ.*現在接続を示しません/,
  );
});

test("accepted handover後はclaimより古いconnected観測を引継確認待ちにする", () => {
  // active aliasの移転だけで旧request接続を新contextへ継承する回帰を防ぐ。
  const registry = {
    active_front_desk: { claimed_at: "2026-09-13T04:05:00Z" },
    handover: { state: "accepted" },
  };
  const current = new Set(["front-desk", "status-owner"]);
  assert.equal(
    requestHasCurrentConnection(request64, current, registry, "2026-09-13T04:06:00Z"),
    false,
  );
  assert.match(
    requestConnectionDescription(request64, current, registry, "2026-09-13T04:06:00Z"),
    /旧観測.*引継確認待ち/,
  );
  const refreshed = { ...request64, runtime_observed_at: "2026-09-13T04:06:00Z" };
  assert.equal(
    requestHasCurrentConnection(refreshed, current, registry, "2026-09-13T04:06:00Z"),
    true,
  );
  assert.match(
    requestConnectionDescription(refreshed, current, registry, "2026-09-13T04:06:00Z"),
    /現在接続を確認/,
  );
});

test("active Front Desk binding不一致では旧rootのruntime表示をすべて隠す", () => {
  // claim成功とstatus更新の間や旧publisher到着時に旧treeがcurrentへ戻る回帰を防ぐ。
  const claimed = {
    ...registrySnapshot,
    active_runtime_bound: true,
    runtime_binding_verified: false,
    request_registry: {
      generation: 4,
      active_front_desk: {
        alias: "front-desk-next",
      },
      requests: [request64],
    },
  };
  assert.equal(runtimeBindingMatchesActiveFrontDesk(claimed), false);
  const hidden = activeRuntimeSnapshot(claimed);
  assert.deepEqual(hidden.items, []);
  assert.equal(hidden.runtime_capacity, null);
  assert.equal(hidden.focus_summary, null);
  assert.equal(hidden.session_tree, null);
  assert.equal(hidden.known_history, null);
  assert.equal(hidden.runtime_binding_confirmed, false);

  const current = {
    ...claimed,
    runtime_binding_verified: true,
  };
  assert.equal(runtimeBindingMatchesActiveFrontDesk(current), true);
  assert.equal(activeRuntimeSnapshot(current), current);
});

test("handover待ちとreconnectableを稼働扱いせず明示dispatch必要と示す", () => {
  // 再開可能な登録状態をworker自動再開やrunningへ格上げする回帰を防ぐ。
  assert.match(requestLifecycleDescription("handover-waiting"), /引継待ち.*明示dispatch必要/);
  assert.match(requestLifecycleDescription("reconnectable"), /再接続可能.*明示dispatch必要/);
});

test("PM task状態はruntime idleと分離しfreshness・不足・PO待ちを正直に示す", () => {
  // agent turn終了をtask完了へ昇格し、古い・不完全なPM recordを隠す回帰を防ぐ。
  assert.equal(statusDescription("idle"), "turn終了");
  assert.equal(requestLifecycleDescription("running"), "進行中");
  assert.notEqual(requestLifecycleDescription("running"), requestLifecycleDescription("completed"));
  const now = Date.parse("2026-09-19T05:02:01Z");
  const board = requestBoardStatus(
    {
      lifecycle: "blocked",
      owner_agent: null,
      next_action: null,
      waiting_on: "external",
      waiting_detail: "正規merge gate decision",
      resume_trigger: null,
      report_updated_at: "2026-09-19T05:00:00Z",
      po_review_required: true,
      po_acceptance_receipt: null,
      closure_audit: { overall: "unknown" },
    },
    now,
  );
  assert.equal(board.freshness, "stale");
  assert.deepEqual(board.missing, ["owner", "next action", "resume trigger"]);
  assert.match(board.waiting, /外部条件待ち.*正規merge gate/);
  assert.equal(board.poState, "PO確認待ち");
  assert.match(board.poDelivery, /未接続.*通知済みとは判定しません/);
  assert.equal(board.closure, "unknown");
});

test("request freshnessは境界内だけfreshで不正clockをunknownにする", () => {
  // future clockやparse不能値で古いrecordをfreshへ見せる回帰を防ぐ。
  const now = Date.parse("2026-09-19T05:02:00Z");
  const base = { lifecycle: "completed", po_review_required: false };
  assert.equal(
    requestBoardStatus({ ...base, report_updated_at: "2026-09-19T05:00:00Z" }, now).freshness,
    "fresh",
  );
  assert.equal(
    requestBoardStatus({ ...base, report_updated_at: "2026-09-19T04:59:59Z" }, now).freshness,
    "stale",
  );
  assert.equal(
    requestBoardStatus({ ...base, report_updated_at: "invalid" }, now).freshness,
    "unknown",
  );
  assert.equal(
    requestBoardStatus({ ...base, report_updated_at: "2026-09-19T05:02:01Z" }, now).freshness,
    "unknown",
  );
});

test("PM観測projectionはcontrol registryと分離して不足とPO待ちを表示する", async () => {
  // claimのないread-only観測を架空Front Desk registryへ昇格する回帰を防ぐ。
  const html = await readFile(new URL("../../docs/status/index.html", import.meta.url), "utf8");
  const board = pmTaskBoardStatus(
    {
      state: "blocked",
      owner: null,
      next_action: null,
      resume_trigger: null,
      waiting_on: "external",
      waiting_detail: "protected merge gate",
      observed_at: "2026-09-19T05:00:00Z",
      po_status: "pending",
    },
    Date.parse("2026-09-19T05:03:00Z"),
  );
  assert.equal(board.freshness, "stale");
  assert.deepEqual(board.missing, ["owner", "next action", "resume trigger"]);
  assert.equal(board.state, "阻害あり");
  assert.match(board.poDelivery, /配送証跡なし.*通知済みとは判定しません/);
  assert.match(html, /id="pm-task-projection"/);
  assert.match(html, /id="control-registry-view"/);
});

test("registry nullは従来snapshotをそのままtreeへ渡す", () => {
  // request_registry未導入snapshotの既存画面を空scopeへ変える回帰を防ぐ。
  assert.equal(requestScopedSnapshot(registrySnapshot, null), registrySnapshot);
  assert.equal(selectedRequestAfterRefresh(null, "request-64"), null);
});

test("依頼一覧は登録状態・Issue観測・runtime時計を分離しnative selectorを使う", async () => {
  // registryの手動記録をruntime観測へ混ぜ、handover readyを稼働表示する回帰を防ぐ。
  const html = await readFile(new URL("../../docs/status/index.html", import.meta.url), "utf8");
  const source = await readFile(new URL("../../docs/status/status.js", import.meta.url), "utf8");
  assert.match(html, /id="request-registry"[^>]*hidden/s);
  assert.match(html, /id="request-list"/);
  assert.ok(html.indexOf('id="health"') < html.indexOf('id="request-registry"'));
  assert.ok(html.indexOf('id="request-registry"') < html.indexOf('id="focus-title"'));
  assert.ok(html.indexOf('id="request-registry"') < html.indexOf('id="capacity-title"'));
  assert.match(source, /selector\.type = "button"/);
  assert.match(source, /"登録状態"/);
  assert.match(source, /"Issue確認"/);
  assert.match(source, /"Issue状態観測"/);
  assert.match(source, /"runtime connection"/);
  assert.match(source, /"runtime観測"/);
  assert.match(source, /準備済みですが稼働中を示しません。再開には明示dispatchが必要/);
  assert.match(source, /focusedRequestId/);
  assert.match(source, /\(restored \|\| selected\)\?\.focus/);
});

test("connected同aliasでもtree観測がrequest runtime観測より古ければscopeしない", () => {
  // 古いsession treeへ新しいconnected記録を重ねて現在接続と表示する回帰を防ぐ。
  const staleTreeSnapshot = {
    ...registrySnapshot,
    session_tree: {
      ...registrySnapshot.session_tree,
      observed_at: "2026-09-13T03:59:59Z",
    },
    request_registry: {
      active_front_desk: { claimed_at: "2026-09-13T04:00:00Z" },
      requests: [request64],
    },
  };
  const scoped = requestScopedSnapshot(staleTreeSnapshot, request64);
  assert.equal(scoped.session_tree, null);
  assert.equal(scoped.request_context.connection_confirmed, false);
  assert.match(
    requestConnectionDescription(
      request64,
      new Set(["front-desk", "status-owner"]),
      staleTreeSnapshot.request_registry,
      staleTreeSnapshot.session_tree.observed_at,
    ),
    /tree観測が古い.*現在接続を示しません/,
  );
});

test("選択request要約はpublic summaryとreport clockへbindしacceptanceを混ぜない", async () => {
  // 59/64切替後もglobal focusや受入条件を今回進捗として見せる回帰を防ぐ。
  assert.deepEqual(requestFocusDescription(request59), {
    purpose: "担当関係を確認する",
    progress: "PR 61を公開済み",
    blocker: "未報告",
    nextAction: "未報告",
    updatedAt: "2026-09-13T03:45:00Z",
    source: "registryの手動公開summary",
  });
  assert.deepEqual(requestFocusDescription(request64), {
    purpose: "新しい窓口へ未完依頼を引き継ぐ",
    progress: "APIとCLIを検証中",
    blocker: "未報告",
    nextAction: "独立review",
    updatedAt: "2026-09-13T04:00:00Z",
    source: "registryの手動公開summary",
  });
  const source = await readFile(new URL("../../docs/status/status.js", import.meta.url), "utf8");
  assert.match(source, /"選択した依頼の要約"/);
  assert.match(source, /runtime\/current treeとは別です/);
  assert.match(source, /renderFocus\(latestSnapshot, request\)/);
});
