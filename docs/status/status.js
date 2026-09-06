const API = "https://api.github.com/repos/yomote/agent-world";
const MILESTONE = "Factory rollout";
const CACHE_KEY = "agent-world-live-status-v1";
const CACHE_MS = 5 * 60 * 1000;
const DETAIL_LIMIT = 4;
const DETAIL_COOLDOWN_MS = 15 * 1000;
const COMMENT_MAX = 600;
const STATUS_ORDER = ["in-progress", "review", "blocked", "ready", "unknown", "done"];
const FILTER_ORDER = [
  "in-progress",
  "review",
  "awaiting-human",
  "blocked",
  "ready",
  "unknown",
  "done",
];
const STATUS_LABELS = {
  "in-progress": "進行中",
  review: "レビュー待ち",
  "awaiting-human": "本人承認待ち",
  blocked: "停止中",
  ready: "Ready",
  unknown: "状態未設定",
  done: "完了",
};

export function statusOf(issue) {
  if (issue.state === "closed") return "done";
  const label = issue.labels.find((item) => item.name.startsWith("status:"));
  const value = label?.name.slice("status:".length).trim();
  return STATUS_ORDER.includes(value) && value !== "done" ? value : "unknown";
}

export function needsHuman(item) {
  return item.labels.some((label) => label.name === "needs-human");
}

export function ownerOf(issue) {
  const label = issue.labels.find((item) => item.name.startsWith("owner:"));
  return label?.name.slice("owner:".length).trim() || "未割当";
}

export function actionResult(run) {
  if (run.status !== "completed") return run.status;
  return run.conclusion || "unknown";
}

export function relatedIssueNumbers(pull) {
  const text = `${pull.title}\n${pull.body || ""}`;
  return [...new Set([...text.matchAll(/(?:^|[\s(:])#(\d+)\b/g)].map((match) => Number(match[1])))];
}

export function elapsed(iso, now = Date.now()) {
  const seconds = Math.max(0, Math.floor((now - Date.parse(iso)) / 1000));
  if (seconds < 60) return `${seconds}秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}時間前`;
  return `${Math.floor(hours / 24)}日前`;
}

export function requestAllowed(now, deadline) {
  return now >= deadline;
}

export function requestDeadline(now, retryAt = 0) {
  return Math.max(now + CACHE_MS, retryAt || 0);
}

async function github(path, { complete = false } = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
  });
  if (!response.ok) {
    const remaining = response.headers.get("x-ratelimit-remaining");
    const retryAfter = response.headers.get("retry-after");
    const reset = response.headers.get("x-ratelimit-reset");
    const error = new Error(`GitHub API ${response.status}`);
    error.rateLimited = response.status === 429 || (response.status === 403 && remaining === "0");
    if (error.rateLimited) {
      const retryAt = retryAfter ? Date.now() + Number(retryAfter) * 1000 : 0;
      const resetAt = reset ? Number(reset) * 1000 : 0;
      error.retryAt = Math.max(retryAt, resetAt, Date.now() + CACHE_MS);
    }
    throw error;
  }
  if (complete && /<[^>]+>;\s*rel="next"/.test(response.headers.get("link") || "")) {
    throw new Error("GitHub API result incomplete: 取得上限を超えました");
  }
  return response.json();
}

async function loadLive() {
  const milestones = await github("/milestones?state=all&per_page=100", { complete: true });
  const milestone = milestones.find((item) => item.title === MILESTONE);
  if (!milestone) throw new Error(`${MILESTONE} milestoneが見つかりません`);
  // GitHubのsecondary rate limitを避けるため、外部requestは直列にする。
  const issues = await github(`/issues?milestone=${milestone.number}&state=all&per_page=100`, {
    complete: true,
  });
  const pulls = await github("/pulls?state=open&per_page=100", { complete: true });
  const runs = await github("/actions/runs?per_page=30");
  return {
    fetchedAt: new Date().toISOString(),
    milestone,
    issues: issues.filter((item) => !item.pull_request),
    pulls,
    runs: runs.workflow_runs,
  };
}

function loadCache() {
  try {
    const value = JSON.parse(localStorage.getItem(CACHE_KEY));
    return value &&
      Number.isFinite(Date.parse(value.fetchedAt)) &&
      Array.isArray(value.issues) &&
      Array.isArray(value.pulls) &&
      Array.isArray(value.runs) &&
      typeof value.milestone?.html_url === "string"
      ? value
      : null;
  } catch {
    return null;
  }
}

function saveCache(data) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(data));
  } catch {
    // private browsing等でstorageが使えなくても、取得済みlive dataは表示する。
  }
}

function text(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

function link(value, href, className) {
  const node = text("a", value, className);
  node.href = href;
  node.rel = "noopener noreferrer";
  return node;
}

function pullsFor(issue, pulls) {
  return pulls.filter((pull) => relatedIssueNumbers(pull).includes(issue.number));
}

function displayOwner(item) {
  const owner = ownerOf(item);
  return owner === "pm" ? "pm（AI PM）" : owner;
}

export function reportFieldsForPull(pull, relatedIssue) {
  const statusSource = pull.labels.some((label) => label.name.startsWith("status:"))
    ? pull
    : relatedIssue || pull;
  const ownerSource = pull.labels.some((label) => label.name.startsWith("owner:"))
    ? pull
    : relatedIssue || pull;
  return {
    status: statusOf({ ...statusSource, state: "open" }),
    owner: ownerOf(ownerSource),
    needsHuman: needsHuman(pull) || Boolean(relatedIssue && needsHuman(relatedIssue)),
  };
}

export function latestRunFor(pull, runs) {
  return runs.find(
    (run) =>
      run.head_sha === pull.head.sha ||
      run.pull_requests.some((item) => item.number === pull.number),
  );
}

function metaRow(dl, name, value) {
  dl.append(text("dt", name));
  const dd = document.createElement("dd");
  if (typeof value === "string") dd.textContent = value;
  else dd.append(value);
  dl.append(dd);
}

let detailRequests = 0;
let nextDetailAllowed = 0;

async function loadLatestComment(issue, target) {
  if (target.dataset.loaded) return;
  if (detailRequests >= DETAIL_LIMIT) {
    target.textContent =
      "この表示sessionの詳細取得上限（4件）に達しました。Issueを開いて確認してください。";
    return;
  }
  if (Date.now() < nextDetailAllowed) {
    const seconds = Math.ceil((nextDetailAllowed - Date.now()) / 1000);
    target.textContent = `API予算保護のため、次の詳細取得まであと${seconds}秒です。`;
    return;
  }
  target.dataset.loaded = "true";
  if (!issue.comments) {
    target.textContent = "報告コメントはありません。";
    return;
  }
  detailRequests += 1;
  nextDetailAllowed = Date.now() + DETAIL_COOLDOWN_MS;
  target.textContent = "最新報告を取得中…";
  try {
    const comments = await github(
      `/issues/${issue.number}/comments?per_page=1&page=${Math.max(1, issue.comments)}`,
    );
    const body = comments[0]?.body || "報告コメントはありません。";
    target.textContent = body.length > COMMENT_MAX ? `${body.slice(0, COMMENT_MAX)}…` : body;
  } catch (error) {
    if (error.retryAt) {
      nextAllowed = Math.max(nextAllowed, error.retryAt);
      nextDetailAllowed = error.retryAt;
      detailRequests = DETAIL_LIMIT;
    }
    target.textContent = `取得失敗: ${error.message}。Issue本文を確認してください。`;
  }
}

function issueCard(issue, data) {
  const status = statusOf(issue);
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.status = status;
  card.dataset.needsHuman = String(needsHuman(issue));
  const top = document.createElement("div");
  top.className = "card-top";
  top.append(text("span", `ISSUE #${issue.number}`, "issue-number"));
  top.append(text("span", STATUS_LABELS[status], "badge"));
  if (needsHuman(issue))
    top.append(text("span", STATUS_LABELS["awaiting-human"], "badge human-text"));
  card.append(top);
  const title = document.createElement("h3");
  title.append(link(issue.title, issue.html_url));
  card.append(title);

  const dl = document.createElement("dl");
  dl.className = "meta";
  metaRow(dl, "owner", displayOwner(issue));
  metaRow(
    dl,
    "最終変更",
    `${new Date(issue.updated_at).toLocaleString("ja-JP")}（${elapsed(issue.updated_at)}）`,
  );
  if (status === "blocked")
    metaRow(dl, "blocker", text("span", "停止理由はIssueの最新報告を確認", "blocked-text"));
  if (needsHuman(issue))
    metaRow(dl, "本人承認", text("span", "ユーザーの判断待ち。AI PMの判断とは区別", "human-text"));
  const related = pullsFor(issue, data.pulls);
  const wrapper = document.createElement("div");
  wrapper.className = "pr-list";
  if (!related.length) wrapper.textContent = "関連open PRなし";
  for (const pull of related) {
    const run = latestRunFor(pull, data.runs);
    const state = pull.draft ? "Draft" : "Ready";
    const result = run ? ` / ${actionResult(run)}` : " / Actions未取得";
    const item = link(`#${pull.number} ${state}${result}`, pull.html_url, "pr-pill");
    item.append(text("span", elapsed(pull.updated_at)));
    wrapper.append(item);
  }
  metaRow(dl, "関連PR", wrapper);
  card.append(dl);

  const details = document.createElement("details");
  details.className = "lazy-detail";
  details.append(text("summary", "最新報告を読む（APIを1回使用）"));
  const note = text("p", "開くと取得します。", "latest-note");
  details.append(note);
  details.addEventListener("toggle", () => {
    if (details.open) loadLatestComment(issue, note);
  });
  card.append(details);
  return card;
}

function renderFilters(data) {
  const filters = document.querySelector("#filters");
  filters.replaceChildren();
  const counts = Object.fromEntries(
    FILTER_ORDER.map((key) => [
      key,
      data.issues.filter((issue) =>
        key === "awaiting-human" ? needsHuman(issue) : statusOf(issue) === key,
      ).length,
    ]),
  );
  for (const key of ["all", ...FILTER_ORDER]) {
    const label =
      key === "all" ? `すべて ${data.issues.length}` : `${STATUS_LABELS[key]} ${counts[key]}`;
    const button = text("button", label, "filter");
    button.type = "button";
    button.dataset.filter = key;
    button.setAttribute("aria-pressed", key === "all" ? "true" : "false");
    button.addEventListener("click", () => {
      for (const item of filters.children)
        item.setAttribute("aria-pressed", String(item === button));
      for (const card of document.querySelectorAll(".card"))
        card.hidden =
          key !== "all" &&
          (key === "awaiting-human"
            ? card.dataset.needsHuman !== "true"
            : card.dataset.status !== key);
    });
    filters.append(button);
  }
}

function renderWork(data) {
  const target = document.querySelector("#work-items");
  target.replaceChildren();
  const open = data.issues
    .filter((issue) => issue.state === "open")
    .sort((a, b) => STATUS_ORDER.indexOf(statusOf(a)) - STATUS_ORDER.indexOf(statusOf(b)));
  const done = data.issues.filter((issue) => issue.state === "closed");
  for (const issue of open) target.append(issueCard(issue, data));
  if (done.length) {
    const details = document.createElement("details");
    details.className = "done-group";
    details.append(text("summary", `完了 ${done.length}件`));
    const grid = document.createElement("div");
    grid.className = "done-grid";
    for (const issue of done) grid.append(issueCard(issue, data));
    details.append(grid);
    target.append(details);
  }
  if (!data.issues.length) target.append(text("p", "対象Issueがありません。", "empty"));
}

function pullCard(pull, data) {
  const issueNumbers = relatedIssueNumbers(pull);
  const relatedIssue = data.issues.find((issue) => issueNumbers.includes(issue.number));
  const report = reportFieldsForPull(pull, relatedIssue);
  const status = report.status;
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.status = status;
  card.dataset.needsHuman = String(report.needsHuman);
  const top = document.createElement("div");
  top.className = "card-top";
  top.append(text("span", `PR #${pull.number}`, "issue-number"));
  top.append(text("span", pull.draft ? "Draft" : "Ready", "badge"));
  if (report.needsHuman)
    top.append(text("span", STATUS_LABELS["awaiting-human"], "badge human-text"));
  card.append(top);
  const title = document.createElement("h3");
  title.append(link(pull.title, pull.html_url));
  card.append(title);
  const dl = document.createElement("dl");
  dl.className = "meta";
  metaRow(dl, "報告状態", STATUS_LABELS[status]);
  metaRow(dl, "owner", report.owner === "pm" ? "pm（AI PM）" : report.owner);
  metaRow(
    dl,
    "最終変更",
    `${new Date(pull.updated_at).toLocaleString("ja-JP")}（${elapsed(pull.updated_at)}）`,
  );
  const run = latestRunFor(pull, data.runs);
  metaRow(
    dl,
    "最新Actions",
    run ? link(`${run.name}: ${actionResult(run)}`, run.html_url) : "関連run未取得",
  );
  if (report.needsHuman)
    metaRow(dl, "本人承認", text("span", "ユーザーの判断待ち。AI PMの判断とは区別", "human-text"));
  if (status === "blocked")
    metaRow(dl, "blocker", link("PRの停止理由を確認", pull.html_url, "blocked-text"));
  card.append(dl);
  return card;
}

function renderPulls(data) {
  const target = document.querySelector("#pull-requests");
  target.replaceChildren();
  const issueNumbers = new Set(data.issues.map((issue) => issue.number));
  const related = data.pulls.filter((pull) =>
    relatedIssueNumbers(pull).some((number) => issueNumbers.has(number)),
  );
  for (const pull of related) target.append(pullCard(pull, data));
  if (!related.length)
    target.append(text("p", "対象Issueに関連するOpen PRはありません。", "empty"));
}

function renderActions(data) {
  const target = document.querySelector("#actions");
  target.replaceChildren();
  for (const run of data.runs.slice(0, 8)) {
    const row = document.createElement("div");
    row.className = "action-row";
    const result = actionResult(run);
    const state = text("span", result, "action-state");
    state.dataset.result = result;
    row.append(state);
    row.append(link(`${run.name} · ${run.head_branch}`, run.html_url));
    row.append(text("time", elapsed(run.updated_at)));
    target.append(row);
  }
  if (!data.runs.length) target.append(text("p", "Actions runがありません。", "empty"));
}

function render(data, mode = "live", message = "GitHub公開データを取得しました。") {
  const notice = document.querySelector(".notice");
  notice.dataset.state = mode;
  document.querySelector("#health").textContent = message;
  document.querySelector("#fetched-at").textContent =
    `${new Date(data.fetchedAt).toLocaleString("ja-JP")}（${elapsed(data.fetchedAt)}）`;
  document.querySelector("#next-refresh").textContent =
    mode === "live" ? "5分以内" : "手動更新待ち";
  document.querySelector("#github-dashboard").href = data.milestone.html_url;
  renderFilters(data);
  renderWork(data);
  renderPulls(data);
  renderActions(data);
}

let nextAllowed = 0;
async function refresh(force = false) {
  const button = document.querySelector("#refresh");
  const cached = loadCache();
  const cacheAge = cached ? Date.now() - Date.parse(cached.fetchedAt) : Infinity;
  if (!force && cached && cacheAge < CACHE_MS) {
    render(cached, "live", "5分以内に取得したcacheを表示しています。");
    nextAllowed = Math.max(nextAllowed, Date.parse(cached.fetchedAt) + CACHE_MS);
    return;
  }
  if (!requestAllowed(Date.now(), nextAllowed)) {
    if (!force) return;
    const seconds = Math.ceil((nextAllowed - Date.now()) / 1000);
    document.querySelector("#health").textContent =
      `API予算保護のため、あと${seconds}秒待ってください。`;
    return;
  }
  button.disabled = true;
  nextAllowed = requestDeadline(Date.now());
  try {
    const data = await loadLive();
    saveCache(data);
    render(data);
  } catch (error) {
    nextAllowed = requestDeadline(Date.now(), error.retryAt);
    if (cached) {
      render(
        cached,
        "stale",
        `最新取得に失敗しました（${error.message}）。前回データをstaleとして表示中です。`,
      );
    } else {
      document.querySelector(".notice").dataset.state = "error";
      document.querySelector("#health").textContent =
        `取得失敗: ${error.message}。成功状態として表示していません。`;
      document
        .querySelector("#work-items")
        .replaceChildren(text("p", "表示できる前回データがありません。", "empty"));
    }
  } finally {
    button.disabled = false;
  }
}

if (typeof document !== "undefined") {
  document.querySelector("#refresh").addEventListener("click", () => refresh(true));
  refresh();
  setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, 60 * 1000);
}
