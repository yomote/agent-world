import assert from "node:assert/strict";
import test from "node:test";
import { buildTree, capacityMetrics, statusDescription } from "../../docs/status/status.js";

test("current treeは可変nodeを全て描画対象にし、履歴を人数へ混ぜない", () => {
  // 固定人数や履歴nodeをcurrent稼働として数える回帰を防ぐ。
  const nodes = buildTree(
    {
      root_agent: "front",
      nodes: [
        { agent: "front", parent_agent: null },
        { agent: "pm", parent_agent: "front" },
        { agent: "worker", parent_agent: "pm" },
      ],
      covered_agents: ["front", "pm", "worker"],
    },
    { entries: [{ agent: "old", parent_agent: "pm", status: "completed" }] },
    [
      { agent: "front", owner_label: "窓口" },
      { agent: "worker", task: "具体依頼" },
    ],
  );
  assert.equal(nodes.length, 4);
  assert.equal(nodes.filter((node) => node.kind === "current").length, 3);
  assert.equal(nodes.find((node) => node.agent === "old").kind, "history");
});

test("空きは明示供給値だけを表示し、max-runningを勝手に計算しない", () => {
  // 上限と実行中だけから起動可能枠を捏造する回帰を防ぐ。
  assert.equal(capacityMetrics({ running: 4, max_concurrent_agents: 8 })[2].value, "未取得");
  assert.equal(capacityMetrics({ running: 4, max_concurrent_agents: 8, available: 4 })[2].value, 4);
});

test("状態ラベルは役割名から推測しない", () => {
  // agent名や親子関係を実行状態に変換する回帰を防ぐ。
  assert.equal(statusDescription("running"), "実装中");
  assert.equal(statusDescription("completed"), "完了");
});
