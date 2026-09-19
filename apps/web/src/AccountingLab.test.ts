import { describe, expect, it } from "vitest";
import { comparisonMessage } from "./accountingComparison";
import type { AccountingRun } from "./api/accountingClient";

const run = (fixtureId: string) => ({ fixture_id: fixtureId }) as AccountingRun;

describe("accounting comparison condition", () => {
  it("同じfixtureだけを同条件比較として表示する", () => {
    // 回帰: latest取得したknownとM8を同条件のAgent/baseline比較に見せない。
    expect(comparisonMessage(run("fixture-known"), run("fixture-known"))).toContain("同じfixture");
    expect(comparisonMessage(run("fixture-known"), run("fixture-m8"))).toContain("比較不可");
    expect(comparisonMessage(run("fixture-known"), null)).toContain("比較不可");
  });
});
