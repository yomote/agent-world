import type { AccountingRun } from "./api/accountingClient";

export function comparisonMessage(
  agent: AccountingRun | null,
  baseline: AccountingRun | null,
): string {
  return agent && baseline && agent.fixture_id === baseline.fixture_id
    ? "同じfixture・typed fact・solver・validatorで結果と負担を比較しています。優位性は未実証です。"
    : "比較不可: fixtureが異なるか片方が未実行です。各カードの条件を揃えてから比較します。";
}
