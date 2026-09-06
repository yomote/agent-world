import assert from "node:assert/strict";
import test from "node:test";

import {
  actionResult,
  elapsed,
  ownerOf,
  relatedIssueNumbers,
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
  assert.equal(statusOf({ state: "open", labels: [{ name: "needs-human" }] }), "awaiting-human");
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
