# ADR <番号>: <製品・インフラの構造決定>

- 状態: Proposed | Accepted | Superseded
- 日付: YYYY-MM-DD

## 対象と背景

製品アプリ、共有product実行基盤、または公開インフラにおける配置、通信、状態所有、信頼境界の問題を記す。

## 決定

採用する構造と責務の境界を記す。

## 代案とトレードオフ

比較した代案、採用しない理由、結果として受け入れる制約を記す。

## 正本への参照

現在の構成値・API契約・実装仕様への参照だけを置く。操作手順、trigger、承認、merge条件、予算、ownerはここに書かず、runbookまたは担当領域のREADMEを正本にする。

GitHub設定、CI、merge、PM、registry、開発automationは製品ADRの対象外であり、`docs/development-operations.md`、runbook、担当領域のREADMEへ記録する。
