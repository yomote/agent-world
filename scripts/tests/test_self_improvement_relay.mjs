import assert from "node:assert/strict";
import { pump } from "../automation/relay.mjs";

// GitHub操作をconnectorへfallbackし、自動再送する回帰を防ぐ。
const request = {
  id: "11111111-1111-4111-8111-111111111111",
  campaign: "runner",
  operation: "normal_merge",
  arguments: {
    repository_full_name: "yomote/agent-world",
    pr_number: 25,
    expected_head_sha: "a".repeat(40),
    merge_method: "squash",
  },
  write: true,
};
const calls = [];
const tools = {
  exec_command: async () => ({ exit_code: 0, output: JSON.stringify(request) }),
  apply_patch: async (patch) => {
    assert.equal(calls.length, 0);
    assert.match(patch, /"status":"unknown"/);
    calls.push("saved");
  },
};
const result = await pump("C:/workspace", "runner", tools);
assert.equal(result.response.status, "unknown");
assert.equal(calls.length, 1);

// reviewを自作せず、固定packetをownerへ返して実回答まで保存を保留する。
request.operation = "independent_review";
tools.apply_patch = async () => {
  assert.fail("review has not returned");
};
assert.deepEqual((await pump("C:/workspace", "runner", tools)).pending_review, request);
