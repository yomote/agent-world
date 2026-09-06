const STATUS_LABELS = {
  running: "実装中",
  "review-wait": "レビュー待ち",
  blocked: "停止中",
};

const SOURCE_LABELS = {
  "codex-event": ["Codex event", "Codex App Server由来の正規化eventです。"],
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
  notice.dataset.state = snapshot.stale ? "stale" : "live";
  document.querySelector("#health").textContent = snapshot.stale
    ? `更新停止の可能性があります（受信から${snapshot.age_seconds}秒）。`
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
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.status = item.status;
    const top = document.createElement("div");
    top.className = "card-top";
    top.append(text("span", item.role, "issue-number"));
    top.append(text("span", STATUS_LABELS[item.status] || item.status, "badge"));
    card.append(top, text("h3", item.agent));
    const dl = document.createElement("dl");
    dl.className = "meta";
    metaRow(dl, "課題", item.task);
    metaRow(
      dl,
      "観測",
      `${new Date(item.observed_at).toLocaleString("ja-JP")}（${elapsed(item.observed_at)}）`,
    );
    metaRow(dl, "Issue", optionalLink("開く", item.issue_url));
    metaRow(dl, "PR", optionalLink("開く", item.pr_url));
    if (item.note) metaRow(dl, item.status === "blocked" ? "停止理由" : "状況", item.note);
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
