const STATUS_LABELS = {
  "not-started": "未着手",
  running: "実装中",
  "review-wait": "レビュー待ち",
  "human-wait": "人の判断待ち",
  stopped: "停止済み",
  completed: "完了",
  // v1 snapshots already stored in Azure remain readable.
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
  "pm-confirmed": [
    "PM確認snapshot",
    "PMが確認した時点の報告です。Codex App Serverとは未接続です。",
  ],
  fixture: ["fixture", "表示確認用データです。実際のagent稼働を示しません。"],
};

export function elapsed(iso, now = Date.now()) {
  const seconds = Math.max(0, Math.floor((now - Date.parse(iso)) / 1000));
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
  return STATUS_LABELS[status] || status;
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
  if (value instanceof Node) dd.append(value);
  else dd.textContent = value;
  dl.append(dd);
}

function optionalLink(label, url) {
  if (!url) return "—";
  const node = text("a", label);
  node.href = url;
  node.rel = "noopener noreferrer";
  return node;
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
  document.querySelector("#fetched-at").textContent =
    `${new Date(snapshot.received_at).toLocaleString("ja-JP")}（${elapsed(snapshot.received_at)}）`;
  document.querySelector("#next-refresh").textContent =
    `${new Date(snapshot.observed_at).toLocaleString("ja-JP")}（${elapsed(snapshot.observed_at)}）`;
  document.querySelector("#source-kind").textContent = sourceLabel;
  document.querySelector("#source-note").textContent = sourceNote;

  const target = document.querySelector("#work-items");
  target.replaceChildren();
  for (const item of snapshot.items) {
    const owner = item.owner_label || item.agent;
    const session = item.session_label || item.role;
    const task = item.task_label || item.task;
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.status = item.status;
    const top = document.createElement("div");
    top.className = "card-top";
    top.append(text("span", session, "issue-number"));
    top.append(text("span", statusDescription(item.status), "badge"));
    card.append(top, text("h3", owner));
    const dl = document.createElement("dl");
    dl.className = "meta";
    metaRow(dl, "課題", task);
    metaRow(
      dl,
      "観測",
      `${new Date(item.observed_at).toLocaleString("ja-JP")}（${elapsed(item.observed_at)}）`,
    );
    if (item.latest_activity_at) {
      const activity = ACTIVITY_LABELS[item.latest_activity] || item.latest_activity;
      metaRow(
        dl,
        "最新activity",
        `${activity} / ${new Date(item.latest_activity_at).toLocaleString("ja-JP")}（${elapsed(item.latest_activity_at)}）`,
      );
    }
    if (item.stale) metaRow(dl, "鮮度", "activity途絶");
    if (item.next_action) metaRow(dl, "次の行動", item.next_action);
    if (item.blocker) metaRow(dl, "blocker", item.blocker);
    metaRow(dl, "Issue", optionalLink("開く", item.issue_url));
    metaRow(dl, "PR", optionalLink("開く", item.pr_url));
    if (item.note) metaRow(dl, "根拠", item.note);
    card.append(dl);
    target.append(card);
  }
  if (!snapshot.items.length) target.append(text("p", "現在の担当はありません。", "empty"));
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
