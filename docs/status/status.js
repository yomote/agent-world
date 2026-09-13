const STATUS_LABELS = {
  "not-started": "未着手",
  running: "実装中",
  "review-wait": "レビュー待ち",
  "human-wait": "人の判断待ち",
  stopped: "停止済み",
  completed: "完了",
  idle: "turn終了",
  unknown: "状態不明",
  blocked: "停止中",
};

const ACTIVITY_LABELS = {
  "session-created": "session作成",
  "task-started": "task開始",
  "task-complete": "turn終了",
  "structured-item": "構造化activity",
};

const SOURCE_LABELS = {
  "codex-event": ["Codex event", "Codex App Server由来の正規化eventです。"],
  "local-event-record": [
    "local event記録",
    "このPCの構造化task event由来です。Codex App Serverへのlive接続ではありません。",
  ],
  "ingest-upsert": [
    "保持付き部分更新",
    "サーバーが既存行を保持し、認可済みingestの指定項目だけを反映したsnapshotです。",
  ],
  "pm-confirmed": [
    "PM確認snapshot",
    "PMが確認した時点の報告です。Codex App Serverとは未接続です。",
  ],
  fixture: ["fixture", "表示確認用データです。実際のagent稼働を示しません。"],
};

const HISTORY_SOURCE_LABELS = {
  "runtime-list-agents-metadata": "runtime list_agents metadata観測",
  "pm-recorded-completed-work-unit": "PMが記録した完了work unit",
};

export function elapsed(iso, now = Date.now()) {
  const parsed = Date.parse(iso);
  if (!Number.isFinite(parsed)) return "未取得";
  const seconds = Math.max(0, Math.floor((now - parsed) / 1000));
  if (seconds < 60) return `${seconds}秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分前`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours}時間前` : `${Math.floor(hours / 24)}日前`;
}

export function sourceDescription(source) {
  return SOURCE_LABELS[source] || ["不明", "未対応の入力種別です。"];
}

export function statusDescription(status) {
  return STATUS_LABELS[status] || status || "未取得";
}

export function summarizeItems(items) {
  const statuses = new Map();
  for (const item of items) statuses.set(item.status, (statuses.get(item.status) || 0) + 1);
  return {
    total: items.length,
    statuses: [...statuses.entries()].map(([status, count]) => ({ status, count })),
  };
}

export function activityDescription(item) {
  if (!item.latest_activity_at) return null;
  return {
    label: ACTIVITY_LABELS[item.latest_activity] || item.latest_activity || "種別未取得",
    observedAt: item.latest_activity_at,
  };
}

export function parentDescription(item, items) {
  if (item.parent_relation === "root") return "依頼元なし（本作業の窓口）";
  if (item.parent_relation !== "delegated" || !item.parent_agent) return "依頼元未取得";
  const parent = items.find((candidate) => candidate.agent === item.parent_agent);
  return parent ? parent.owner_label || parent.agent : `${item.parent_agent}（snapshot外）`;
}

export function parentSourceDescription(source) {
  if (source === "runtime-canonical-task-path") return "runtime canonical path観測";
  if (source === "explicit-delegation") return "明示された委任";
  return "未取得";
}

export function relationshipEdges(items) {
  return items.map((item) => ({
    agent: item.agent,
    owner: item.owner_label || item.agent,
    relation: item.parent_relation || "unknown",
    parent: parentDescription(item, items),
    parentAgent: item.parent_agent || null,
  }));
}

export function capacityDescription(capacity) {
  if (!capacity) {
    return {
      headline: "このセッション：観測時点の実行中 未取得 / 同時実行上限 未取得",
      note: "runtimeの実行枠snapshotはまだ受信していません。",
      rows: [],
      metrics: [
        { label: "稼働", value: "未取得" },
        { label: "同時実行上限", value: "未取得" },
        { label: "空き", value: "未取得", note: "明示供給なし" },
      ],
    };
  }
  const running = capacity.running ?? "未取得";
  const limit = capacity.max_concurrent_agents ?? "未取得";
  const availabilitySupplied =
    capacity.available !== null &&
    capacity.available !== undefined &&
    capacity.availability_source === "derived-running-limit" &&
    capacity.availability_definition === "max-concurrent-minus-running";
  const rows = [
    ["対象範囲", capacity.scope],
    [
      "実行状態の出所",
      capacity.state_source === "runtime-list-agents-metadata"
        ? "実行状態の観測（集計のみ）"
        : "未取得",
    ],
    [
      "同時実行上限の出所",
      capacity.limit_source === "runtime-instructions" ? "セッション設定／明示入力" : "未取得",
    ],
    [
      "容量観測",
      capacity.observed_at
        ? `${new Date(capacity.observed_at).toLocaleString("ja-JP")}（${elapsed(capacity.observed_at)}）`
        : "未取得",
    ],
  ];
  if (capacity.total !== null && capacity.total !== undefined) {
    rows.push(["存在総数", `${capacity.total}（実行中とは別）`]);
  }
  for (const [label, value] of [
    ["idle", capacity.idle],
    ["completed", capacity.completed],
  ]) {
    if (value !== null && value !== undefined) rows.push([`${label}（観測値）`, String(value)]);
  }
  rows.push([
    "空きの出所・定義",
    availabilitySupplied ? "実行中と上限の明示観測から算出（max concurrent − running）" : "未取得",
  ]);
  return {
    headline: `このセッション：観測時点の実行中 ${running} / 同時実行上限 ${limit}`,
    note: availabilitySupplied
      ? "空きは観測値の差です。次のagentを起動できることは保証しません。"
      : "空きは明示供給されていません。上限や担当行から推測しません。",
    rows,
    metrics: [
      { label: "稼働", value: running },
      { label: "同時実行上限", value: limit },
      {
        label: "空き",
        value: availabilitySupplied ? capacity.available : "未取得",
        note: availabilitySupplied ? "観測時点の差・起動保証なし" : "明示供給なし",
      },
    ],
  };
}

export function capacityMetrics(capacity) {
  return capacityDescription(capacity).metrics;
}

function validClock(value) {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

export function sessionTreeDescription(tree) {
  const valid = Boolean(
    tree &&
    tree.source === "runtime-list-agents-metadata" &&
    validClock(tree.observed_at) &&
    typeof tree.scope === "string" &&
    tree.scope &&
    typeof tree.root_agent === "string" &&
    Array.isArray(tree.nodes) &&
    Array.isArray(tree.covered_agents),
  );
  return {
    valid,
    source: valid ? "runtime list_agents metadata観測" : "未取得",
    observedAt: valid ? tree.observed_at : null,
    scope: valid ? tree.scope : "未取得",
  };
}

export function buildTree(tree, history = {}, items = []) {
  if (!tree || !Array.isArray(tree.nodes)) return [];
  const rows = new Map(items.map((item) => [item.agent, item]));
  const current = [];
  const currentAgents = new Set();
  for (const node of tree.nodes) {
    if (!node?.agent || currentAgents.has(node.agent)) continue;
    currentAgents.add(node.agent);
    current.push({ ...node, kind: "current", item: rows.get(node.agent) });
  }
  const historyAgents = new Set();
  const past = [];
  for (const entry of history?.entries || []) {
    if (!entry?.agent || currentAgents.has(entry.agent) || historyAgents.has(entry.agent)) continue;
    historyAgents.add(entry.agent);
    past.push({ ...entry, kind: "history", item: rows.get(entry.agent) });
  }
  return [...current, ...past];
}

export function treeLayout(nodes, rootAgent) {
  const byAgent = new Map(nodes.map((node) => [node.agent, node]));
  const children = new Map(nodes.map((node) => [node.agent, []]));
  for (const node of nodes) {
    if (node.parent_agent && children.has(node.parent_agent)) {
      children.get(node.parent_agent).push(node.agent);
    }
  }
  const placed = [];
  const visited = new Set();
  let row = 0;
  const walk = (agent, depth) => {
    if (!byAgent.has(agent) || visited.has(agent)) return;
    visited.add(agent);
    const node = byAgent.get(agent);
    placed.push({ ...node, x: 28 + depth * 244, y: 28 + row * 116 });
    row += 1;
    for (const child of children.get(agent) || []) walk(child, depth + 1);
  };
  walk(rootAgent, 0);
  for (const node of nodes) walk(node.agent, 0);
  return placed;
}

function text(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

function metaRow(dl, name, value) {
  dl.append(text("dt", name));
  const dd = document.createElement("dd");
  if (typeof Node !== "undefined" && value instanceof Node) dd.append(value);
  else dd.textContent = value ?? "未取得";
  dl.append(dd);
}

function optionalLink(label, url) {
  if (!url) return "—";
  const node = text("a", label);
  node.href = url;
  node.rel = "noopener noreferrer";
  return node;
}

const nodeLabel = (node) => node.item?.owner_label || node.agent;
const nodeRequest = (node) =>
  node.item?.instruction_summary || node.item?.task_label || node.item?.task || "具体依頼は未報告";
const nodeStage = (node) =>
  node.kind === "history" ? "履歴・完了" : statusDescription(node.item?.status);

function dated(value) {
  return value ? `${new Date(value).toLocaleString("ja-JP")}（${elapsed(value)}）` : "未取得";
}

function renderDetail(node, allItems) {
  const target = document.querySelector("#node-detail");
  target.replaceChildren(text("h3", `${nodeLabel(node)} — ${nodeStage(node)}`));
  const dl = document.createElement("dl");
  dl.className = "detail-meta";
  metaRow(dl, "依頼", nodeRequest(node));
  metaRow(dl, "目的・課題", node.item?.task_label || node.item?.task || "未取得");
  metaRow(
    dl,
    "依頼元",
    node.item ? parentDescription(node.item, allItems) : node.parent_agent || "未取得",
  );
  metaRow(
    dl,
    "関係の出所",
    node.item ? parentSourceDescription(node.item.parent_source) : "履歴記録",
  );
  metaRow(dl, "関係観測", dated(node.item?.parent_observed_at));
  metaRow(
    dl,
    "現在",
    node.kind === "history"
      ? "履歴のため現在作業はありません"
      : node.item?.current_action || "未取得",
  );
  metaRow(
    dl,
    "確認済み成果",
    node.item?.progress_summary ||
      (node.kind === "history" ? "完了記録（成果詳細は未取得）" : "未取得"),
  );
  metaRow(dl, "残り次手", node.item?.next_action || "未取得");
  metaRow(dl, "阻害", node.item?.blocker || "未報告");
  metaRow(dl, "最終報告", dated(node.item?.summary_updated_at));
  metaRow(dl, "状態観測", dated(node.item?.observed_at));
  const activity = node.item ? activityDescription(node.item) : null;
  metaRow(
    dl,
    "最新activity",
    activity ? `${activity.label} / ${dated(activity.observedAt)}` : "未取得",
  );
  if (node.item?.stale) metaRow(dl, "鮮度", "activity途絶");
  if (node.kind === "history") {
    metaRow(dl, "履歴の出所", HISTORY_SOURCE_LABELS[node.source] || "未取得");
    metaRow(dl, "履歴の最終観測", dated(node.last_observed_at));
  }
  metaRow(dl, "Issue", optionalLink("開く", node.item?.issue_url));
  metaRow(dl, "PR", optionalLink("開く", node.item?.pr_url));
  if (node.item?.note) metaRow(dl, "根拠", node.item.note);
  target.append(dl);
}

function renderTree(snapshot) {
  const svg = document.querySelector("#session-tree");
  const detail = document.querySelector("#node-detail");
  const treeMeta = document.querySelector("#tree-meta");
  svg.replaceChildren();
  treeMeta.replaceChildren();
  const description = sessionTreeDescription(snapshot.session_tree);
  if (!description.valid) {
    document.querySelector("#tree-coverage").textContent = "current tree 未取得";
    detail.textContent = "このscopeのcurrent session treeは未取得です。";
    for (const name of ["対象範囲", "treeの出所", "tree観測"]) metaRow(treeMeta, name, "未取得");
    return;
  }
  const tree = snapshot.session_tree;
  const nodes = treeLayout(
    buildTree(tree, snapshot.known_history, snapshot.items),
    tree.root_agent,
  );
  const covered = new Set(tree.covered_agents);
  document.querySelector("#tree-coverage").textContent =
    `current coverage ${covered.size}/${tree.nodes.length} node`;
  metaRow(treeMeta, "対象範囲", description.scope);
  metaRow(treeMeta, "treeの出所", description.source);
  metaRow(treeMeta, "tree観測", dated(description.observedAt));
  metaRow(
    treeMeta,
    "既知履歴",
    snapshot.known_history?.recorded_at
      ? `${snapshot.known_history.entries?.length || 0}件 / 記録 ${dated(snapshot.known_history.recorded_at)}`
      : "未取得",
  );
  if (!nodes.length) {
    detail.textContent = "表示できるnodeがありません。";
    return;
  }
  const width = Math.max(600, ...nodes.map((node) => node.x + 216));
  const height = Math.max(170, ...nodes.map((node) => node.y + 90));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  const ns = "http://www.w3.org/2000/svg";
  for (const parent of nodes) {
    const children = nodes.filter((node) => node.parent_agent === parent.agent);
    if (!children.length) continue;
    const trunkX = parent.x + 222;
    const parentY = parent.y + 38;
    const childYs = children.map((child) => child.y + 38);
    const segment = (x1, y1, x2, y2, className) => {
      const line = document.createElementNS(ns, "line");
      for (const [name, value] of Object.entries({ x1, y1, x2, y2 }))
        line.setAttribute(name, value);
      line.setAttribute("class", className);
      svg.append(line);
    };
    segment(parent.x + 204, parentY, trunkX, parentY, "tree-line");
    segment(
      trunkX,
      Math.min(parentY, ...childYs),
      trunkX,
      Math.max(parentY, ...childYs),
      "tree-line",
    );
    for (const child of children) {
      segment(
        trunkX,
        child.y + 38,
        child.x,
        child.y + 38,
        child.kind === "history" ? "tree-line history-line" : "tree-line",
      );
    }
  }
  const select = (group, node) => {
    for (const candidate of svg.querySelectorAll(".tree-node"))
      candidate.classList.remove("selected");
    group.classList.add("selected");
    renderDetail(node, snapshot.items);
    detail.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  for (const node of nodes) {
    const group = document.createElementNS(ns, "g");
    group.setAttribute(
      "class",
      `tree-node ${node.kind}${covered.has(node.agent) ? " covered" : ""}`,
    );
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `${nodeLabel(node)}、${nodeStage(node)}。詳細を開く`);
    group.setAttribute("transform", `translate(${node.x} ${node.y})`);
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("width", "204");
    rect.setAttribute("height", "78");
    rect.setAttribute("rx", "10");
    const title = document.createElementNS(ns, "text");
    title.setAttribute("x", "12");
    title.setAttribute("y", "22");
    title.setAttribute("class", "tree-title");
    title.textContent = nodeLabel(node).slice(0, 25);
    const task = document.createElementNS(ns, "text");
    task.setAttribute("x", "12");
    task.setAttribute("y", "44");
    task.setAttribute("class", "tree-task");
    task.textContent = nodeRequest(node).slice(0, 27);
    const state = document.createElementNS(ns, "text");
    state.setAttribute("x", "12");
    state.setAttribute("y", "66");
    state.setAttribute("class", "tree-state");
    state.textContent = `${node.kind === "history" ? "履歴 / " : "current / "}${nodeStage(node)}`;
    group.append(rect, title, task, state);
    group.addEventListener("click", () => select(group, node));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select(group, node);
      }
    });
    svg.append(group);
  }
  const first = svg.querySelector(".tree-node");
  first?.classList.add("selected");
  renderDetail(nodes[0], snapshot.items);
}

function render(snapshot) {
  const [sourceLabel, sourceNote] = sourceDescription(snapshot.source);
  const notice = document.querySelector(".notice");
  const hasStaleItem = snapshot.items.some((item) => item.stale);
  notice.dataset.state = snapshot.stale || hasStaleItem ? "stale" : "live";
  document.querySelector("#health").textContent = snapshot.stale
    ? `snapshot更新停止の可能性があります（受信から${snapshot.age_seconds}秒）。`
    : hasStaleItem
      ? "snapshotは届いていますが、activityが途絶したsessionがあります。"
      : "最新snapshotを表示しています。";
  document.querySelector("#fetched-at").textContent = dated(snapshot.received_at);
  document.querySelector("#next-refresh").textContent = dated(snapshot.observed_at);
  document.querySelector("#source-kind").textContent = sourceLabel;
  document.querySelector("#source-note").textContent = sourceNote;

  const focusMeta = document.querySelector("#focus-meta");
  focusMeta.replaceChildren();
  if (snapshot.focus_summary) {
    metaRow(focusMeta, "作業目的", snapshot.focus_summary.purpose);
    metaRow(focusMeta, "確認済み進捗", snapshot.focus_summary.progress_summary);
    metaRow(focusMeta, "阻害", snapshot.focus_summary.blocker || "未報告");
    metaRow(focusMeta, "次の行動", snapshot.focus_summary.next_action);
    document.querySelector("#focus-updated").textContent =
      `報告 ${dated(snapshot.focus_summary.updated_at)}`;
    document.querySelector("#focus-source").textContent =
      "出典：公開用に明示された今回要約（他の担当行から推測していません）";
  } else {
    for (const name of ["作業目的", "確認済み進捗", "阻害", "次の行動"])
      metaRow(focusMeta, name, "未取得");
    document.querySelector("#focus-updated").textContent = "報告時刻 未取得";
    document.querySelector("#focus-source").textContent = "今回要約は未取得です。";
  }

  const capacity = capacityDescription(snapshot.runtime_capacity);
  const metrics = document.querySelector("#capacity-metrics");
  metrics.replaceChildren();
  for (const metric of capacity.metrics) {
    const box = document.createElement("div");
    box.className = "metric";
    box.append(text("strong", metric.value), text("span", metric.label));
    if (metric.note) box.append(text("small", metric.note));
    metrics.append(box);
  }
  document.querySelector("#capacity-observed").textContent = snapshot.runtime_capacity?.observed_at
    ? `容量観測 ${dated(snapshot.runtime_capacity.observed_at)}`
    : "容量観測 未取得";
  document.querySelector("#capacity-note").textContent = capacity.note;
  const capacityMeta = document.querySelector("#capacity-meta");
  capacityMeta.replaceChildren();
  for (const [name, value] of capacity.rows) metaRow(capacityMeta, name, value);
  renderTree(snapshot);
}

async function refresh() {
  const button = document.querySelector("#refresh");
  button.disabled = true;
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`管理backend HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    document.querySelector(".notice").dataset.state = "error";
    document.querySelector("#health").textContent = `取得失敗: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

if (typeof document !== "undefined") {
  document.querySelector("#refresh").addEventListener("click", refresh);
  refresh();
  setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, 10_000);
}
