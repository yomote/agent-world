# 業務Agentデモのuse case screen

状態: **Research / 選定済み、効果未検証**。調査日: 2026-09-18。

## 結論

最初の業務デモには、会計の「消込例外を調査して経理レビューpackageを作る」を選ぶ。これは市場性やAgentの優位を証明した判断ではない。実務上の例外が資料横断を必要とし、誤適用を避けたまま小さな合成Worldで成果物と反証条件を測れるためである。

AI SREは5業務とは別の横断運用アプリ候補として、「read-onlyのインシデント証拠収集・動的トリアージ」に限定して残す。今回の会計デモへ埋め込まず、別Issueも作らない。製造の品質nonconformity調査は次候補だが、参照した品質監査guidanceを製造業全般のAgent需要へ一般化できるかは未確認である。

## 6カテゴリのscreen

| カテゴリ   | 候補                                 | 既存自動化とAgent仮説                                     | 判断                                       |
| ---------- | ------------------------------------ | --------------------------------------------------------- | ------------------------------------------ |
| 物流       | 配送例外の調査と解決案               | TMS/ERPの例外workflowが強い。複数情報を横断する余地はある | 中。既存rule物流デモを保持                 |
| 小売       | 欠品・backorder・代替判断            | reservation/backorder/reorder ruleが通常処理を広く扱う    | 低〜中。Agent固有価値の根拠が不足          |
| 製造       | 品質nonconformityの根因調査          | 証拠・原因・是正・効果確認は動的調査に合う                | 高。ただし品質責任が重く今回はscreen止まり |
| 金融       | AML/KYC alertの文脈調査              | 証拠横断の余地は大きいが規制・秘匿・見逃しriskが高い      | 中〜高。read-only evidence pack以外は保留  |
| 会計       | 入金消込準備・差異調査               | ERP ruleで解けない略称、合算、資料不足、取消履歴を調べる  | **第一業務デモ**                           |
| 横断AI SRE | インシデント証拠収集・動的トリアージ | 既知障害はrunbookで十分。未知症状の観測選択を比較できる   | **独立枠**                                 |

## 会計を選んだ根拠と限界

[AFPのPayments Guide](https://www.afponline.org/docs/default-source/default-document-library/2020paymentsguide-managingar_reducingdso-final.pdf)は、複数請求の一括支払、remittance情報不足、支払者や請求先の不明、控除理由の不明をcash applicationの実務課題として説明する。これは業務の存在を支える専門資料であり、Agent導入効果や企業全体の発生頻度を示す比較研究ではない。

[Oracle Receivables](https://docs.oracle.com/en/cloud/saas/financials/26b/faofc/how-recommendations-for-receipt-application-are-calculated.html)は参照項目、threshold、under/over payment exceptionを用いるmatchingを、[Dynamics 365](https://learn.microsoft.com/en-us/dynamics365/finance/accounts-receivable/automatic-settlement-prioritization)は自動settlement、優先順位、partial settlementを提供する。したがって通常処理をLLMへ置き換えない。Agent候補は、複数資料の探索、矛盾する版の確認、必要な質問、根拠不足による未配分、レビューpackage作成へ絞る。

固定workflowと同品質ならAgentは不要である。未知bundleで、同じLLM抽出、候補solver、tool、権限、人回答、予算を与えたbaselineと比較し、誤消込を増やさず、適切な保留、必要質問、根拠完全性、手動修正を改善できるかを測る。効果値は未測定である。

## SREを別枠にする根拠

[Google SRE Incident Management Guide](https://sre.google/resources/practices-and-processes/incident-management-guide/)と[Effective Troubleshooting](https://sre.google/sre-book/effective-troubleshooting/)は、alert、playbook、logs/metrics/traces、仮説検証、mitigation、役割、postmortemを扱う。最小候補はread-only investigatorとして証拠、仮説、反証、未確認事項、handoffを作ることとする。変更、rollback、traffic diversionの実行は含めない。この資料もAgentと人やrunbookの効果を直接比較した研究ではない。

## 採用判断の扱い

- 調査範囲: 一次・専門資料9件を中心にした限定screenであり、全業界の絶対順位ではない。
- 実務根拠: 会計例外と既存automationの存在は確認した。
- 合意方向: packageだけで価値を測る最初の縦切りを実装候補にする。
- 未確認: 対象企業での頻度、市場規模、Agentによる削減率、baselineに対する優位。
- 反証: 固定workflowとsolverで同品質、またはAgentが誤配分・不要質問・修正量を増やすなら通常自動化を採用する。

詳細設計は[会計消込Agent提案](../proposals/accounting-cash-application-agent.md)、全体の位置づけは[Agent orchestration roadmap](../proposals/agent-orchestration-domain-roadmap.md)を参照する。
