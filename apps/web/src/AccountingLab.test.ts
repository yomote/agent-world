import { describe, expect, it } from "vitest";
import { comparisonMessage } from "./accountingComparison";
import { accountingCaseLabel, accountingStateLabel } from "./AccountingLab";
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

describe("saved accounting run labels", () => {
  it("fixture hashやrunningを主題にせず、業務上の意味と確認待ちを示す", () => {
    // 回帰: 初見利用者へ内部fixture/statusだけを見せ、M8を成功成果物に見せない。
    const m8 = {
      fixture_id: "fixture-3dff6a560d89",
      status: "running",
      pending_question: { question_id: "q" },
    } as AccountingRun;
    expect(accountingCaseLabel(m8)).toContain("同額請求2件");
    expect(accountingStateLabel(m8)).toBe("人の確認待ち・成果物なし");
  });
});
