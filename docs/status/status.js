const STATUS = {
  running: "実装中",
  completed: "完了",
  "review-wait": "レビュー待ち",
  "human-wait": "人の判断待ち",
  blocked: "停止中",
  unknown: "状態不明",
  stopped: "停止済み",
  "not-started": "未着手",
};
export function elapsed(iso, now = Date.now()) {
  const s = Math.max(0, Math.floor((now - Date.parse(iso)) / 1000));
  if (s < 60) return `${s}秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}分前`;
  return `${Math.floor(m / 60)}時間前`;
}
export function statusDescription(status) {
  return STATUS[status] || status || "未取得";
}
export function capacityMetrics(capacity) {
  const metric = (label, value, note = "") => ({ label, value: value ?? "未取得", note });
  return capacity
    ? [
        metric("稼働", capacity.running),
        metric("同時実行上限", capacity.max_concurrent_agents),
        metric(
          "空き（実行中差引）",
          capacity.available,
          capacity.available === undefined
            ? "明示供給なし"
            : "観測時点の上限−実行中。起動可否は保証しない",
        ),
      ]
    : [metric("稼働", null), metric("同時実行上限", null), metric("空き（実行中差引）", null)];
}
export function buildTree(tree, history = {}, items = []) {
  if (!tree) return [];
  const rows = new Map(items.map((item) => [item.agent, item]));
  const current = tree.nodes.map((node) => ({
    ...node,
    kind: "current",
    item: rows.get(node.agent),
  }));
  const past = (history.entries || [])
    .filter((entry) => !current.some((node) => node.agent === entry.agent))
    .map((entry) => ({ ...entry, kind: "history", item: rows.get(entry.agent) }));
  return [...current, ...past];
}
const el = (tag, value, className) => {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
};
const meta = (dl, label, value) => dl.append(el("dt", label), el("dd", value ?? "未報告"));
const label = (node) => node.item?.owner_label || node.agent;
const request = (node) =>
  node.item?.instruction_summary ||
  node.item?.task_label ||
  node.item?.task ||
  "公開用の具体依頼は未報告";
const stage = (node) =>
  node.kind === "history" ? "履歴・完了" : statusDescription(node.item?.status);
function positions(nodes, root) {
  const children = new Map(nodes.map((node) => [node.agent, []]));
  for (const node of nodes)
    if (node.parent_agent && children.has(node.parent_agent))
      children.get(node.parent_agent).push(node);
  const out = [];
  let row = 0;
  const walk = (agent, depth) => {
    const node = nodes.find((candidate) => candidate.agent === agent);
    if (!node) return;
    out.push({ ...node, x: 34 + depth * 250, y: 34 + row++ * 126 });
    for (const child of children.get(agent) || []) walk(child.agent, depth + 1);
  };
  walk(root, 0);
  for (const node of nodes)
    if (!out.some((entry) => entry.agent === node.agent)) walk(node.agent, 0);
  return out;
}
function detail(node) {
  const target = document.querySelector("#node-detail");
  target.replaceChildren(el("h3", `${label(node)} — ${stage(node)}`));
  const dl = document.createElement("dl");
  dl.className = "detail-meta";
  meta(dl, "依頼", request(node));
  meta(
    dl,
    "現在",
    node.item?.current_action ||
      (node.kind === "history" ? "履歴のため現在作業はありません" : "未報告"),
  );
  meta(
    dl,
    "完了",
    node.kind === "history" || node.item?.status === "completed" ? "完了" : "未完了",
  );
  meta(dl, "残り次手", node.item?.next_action || "未報告");
  meta(dl, "阻害", node.item?.blocker === null ? "なしと明示" : node.item?.blocker || "未報告");
  meta(
    dl,
    "最終報告",
    node.item?.summary_updated_at
      ? `${new Date(node.item.summary_updated_at).toLocaleString("ja-JP")}（${elapsed(node.item.summary_updated_at)}）`
      : node.last_observed_at
        ? `${new Date(node.last_observed_at).toLocaleString("ja-JP")}（履歴観測）`
        : "未報告",
  );
  target.append(dl);
}
function renderTree(snapshot) {
  const svg = document.querySelector("#session-tree");
  svg.replaceChildren();
  const tree = snapshot.session_tree;
  if (!tree) {
    document.querySelector("#tree-coverage").textContent = "current tree 未取得";
    document.querySelector("#node-detail").textContent =
      "このscopeのcurrent session treeは未取得です。";
    return;
  }
  const nodes = positions(buildTree(tree, snapshot.known_history, snapshot.items), tree.root_agent);
  document.querySelector("#tree-coverage").textContent =
    `current coverage ${tree.covered_agents.length}/${tree.nodes.length} node`;
  const width = Math.max(620, ...nodes.map((node) => node.x + 220));
  const height = Math.max(180, ...nodes.map((node) => node.y + 100));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  const ns = "http://www.w3.org/2000/svg";
  const agents = new Map(nodes.map((node) => [node.agent, node]));
  for (const node of nodes)
    if (node.parent_agent && agents.has(node.parent_agent)) {
      const parent = agents.get(node.parent_agent);
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", parent.x + 206);
      line.setAttribute("y1", parent.y + 37);
      line.setAttribute("x2", node.x);
      line.setAttribute("y2", node.y + 37);
      line.setAttribute("class", node.kind === "history" ? "tree-line history-line" : "tree-line");
      svg.append(line);
    }
  for (const node of nodes) {
    const group = document.createElementNS(ns, "g");
    group.setAttribute("class", `tree-node ${node.kind}`);
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `${label(node)}、${stage(node)}。詳細を開く`);
    group.setAttribute("transform", `translate(${node.x} ${node.y})`);
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("width", "206");
    rect.setAttribute("height", "76");
    rect.setAttribute("rx", "10");
    const title = document.createElementNS(ns, "text");
    title.setAttribute("x", "12");
    title.setAttribute("y", "23");
    title.setAttribute("class", "tree-title");
    title.textContent = label(node);
    const task = document.createElementNS(ns, "text");
    task.setAttribute("x", "12");
    task.setAttribute("y", "43");
    task.setAttribute("class", "tree-task");
    task.textContent = request(node).slice(0, 26);
    const state = document.createElementNS(ns, "text");
    state.setAttribute("x", "12");
    state.setAttribute("y", "63");
    state.setAttribute("class", "tree-state");
    state.textContent = stage(node);
    group.append(rect, title, task, state);
    group.addEventListener("click", () => detail(node));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        detail(node);
      }
    });
    svg.append(group);
  }
  detail(nodes[0]);
}
function render(snapshot) {
  const source = snapshot.source || "不明";
  document.querySelector("#source-kind").textContent = source;
  document.querySelector("#source-note").textContent = "公開snapshotの入力種別です。";
  document.querySelector("#health").textContent = snapshot.stale
    ? `snapshot更新停止の可能性があります（受信から${snapshot.age_seconds}秒）。`
    : "最新snapshotを表示しています。";
  document.querySelector("#fetched-at").textContent = new Date(snapshot.received_at).toLocaleString(
    "ja-JP",
  );
  document.querySelector("#next-refresh").textContent = new Date(
    snapshot.observed_at,
  ).toLocaleString("ja-JP");
  const focus = document.querySelector("#focus-meta");
  focus.replaceChildren();
  const s = snapshot.focus_summary;
  for (const [key, value] of [
    ["作業目的", s?.purpose],
    ["確認済み進捗", s?.progress_summary],
    ["阻害", s?.blocker === null ? "なしと明示" : s?.blocker],
    ["次の行動", s?.next_action],
  ])
    meta(focus, key, value || "未報告");
  document.querySelector("#focus-updated").textContent = s?.updated_at
    ? `最終報告 ${new Date(s.updated_at).toLocaleString("ja-JP")}（${elapsed(s.updated_at)}）`
    : "最終報告 未報告";
  const metrics = document.querySelector("#capacity-metrics");
  metrics.replaceChildren();
  for (const value of capacityMetrics(snapshot.runtime_capacity)) {
    const box = document.createElement("div");
    box.className = "metric";
    box.append(el("strong", value.value), el("span", value.label), el("small", value.note));
    metrics.append(box);
  }
  document.querySelector("#capacity-observed").textContent = snapshot.runtime_capacity
    ? `容量観測 ${new Date(snapshot.runtime_capacity.observed_at).toLocaleString("ja-JP")}（${elapsed(snapshot.runtime_capacity.observed_at)}）`
    : "容量観測 未取得";
  document.querySelector("#capacity-note").textContent =
    "空きは明示供給された実行中差引だけです。タスク件数や履歴からは計算しません。";
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
    document.querySelector("#health").textContent = `取得失敗: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}
if (typeof document !== "undefined") {
  document.querySelector("#refresh").addEventListener("click", refresh);
  refresh();
}
