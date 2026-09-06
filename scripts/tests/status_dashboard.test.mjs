import assert from "node:assert/strict";
import test from "node:test";

import {
  actionResult,
  elapsed,
  latestRunFor,
  needsHuman,
  ownerOf,
  relatedIssueNumbers,
  reportFieldsForPull,
  requestAllowed,
  requestDeadline,
  statusOf,
} from "../../docs/status/status.js";

test("skipped Actionsをsuccessに見せない", () => {
  // skipped jobを緑の成功として表示する回帰を防ぐ。
  assert.equal(actionResult({ status: "completed", conclusion: "skipped" }), "skipped");
  assert.equal(actionResult({ status: "in_progress", conclusion: null }), "in_progress");
});

test("Issue labelを状態とownerの正本として読む", () => {
  // 本文の古い状態を表示し続ける回帰を防ぐ。
  const issue = {
    state: "open",
    labels: [{ name: "status: blocked" }, { name: "owner: factory" }],
  };
  assert.equal(statusOf(issue), "blocked");
  assert.equal(ownerOf(issue), "factory");
  assert.equal(statusOf({ ...issue, state: "closed" }), "done");
  assert.equal(statusOf({ state: "open", labels: [{ name: "needs-human" }] }), "unknown");
  assert.equal(
    needsHuman({ labels: [{ name: "needs-human" }, { name: "status: blocked" }] }),
    true,
  );
});

test("PRのstatus・owner・human承認をfieldごとに解決する", () => {
  // ownerだけをPRに付けたとき、Issueのblocked状態をReadyへ変える回帰を防ぐ。
  const issue = {
    state: "open",
    labels: [{ name: "status: blocked" }, { name: "owner: azure" }, { name: "needs-human" }],
  };
  const pull = { labels: [{ name: "owner: factory" }] };
  assert.deepEqual(reportFieldsForPull(pull, issue), {
    status: "blocked",
    owner: "factory",
    needsHuman: true,
  });
});

test("取得失敗後は期限までrequestを許可しない", () => {
  // 失敗の1分後に自動timerがAPIを再送する回帰と、Retry-Afterを破る回帰を防ぐ。
  const now = 1_000_000;
  const deadline = requestDeadline(now, now + 10 * 60 * 1000);
  assert.equal(requestAllowed(now + 60 * 1000, deadline), false);
  assert.equal(requestAllowed(now + 10 * 60 * 1000, deadline), true);
});

test("PRのActionsをbranch名だけで関連付けない", () => {
  // forkに同名branchがあると別PRのrunを表示する回帰を防ぐ。
  const pull = { number: 13, head: { sha: "expected" } };
  const wrong = { head_sha: "other", head_branch: "same", pull_requests: [] };
  const right = { head_sha: "expected", head_branch: "same", pull_requests: [] };
  assert.equal(latestRunFor(pull, [wrong, right]), right);
});

test("PR本文のIssue参照を重複なく関連付ける", () => {
  // 同じIssueを複数回参照したPRを重複表示する回帰を防ぐ。
  assert.deepEqual(
    relatedIssueNumbers({ title: "Status (#7)", body: "Closes #7 and helps #8" }),
    [7, 8],
  );
});

test("経過時間を未来日時でも負数にしない", () => {
  assert.equal(elapsed("2026-09-06T00:01:00Z", Date.parse("2026-09-06T00:00:00Z")), "0秒前");
});
