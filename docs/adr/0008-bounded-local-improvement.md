# 0008. 明示起動するbounded local改善runner

- Status: Accepted
- Date: 2026-09-08

## 背景

Mind Inbox由来の改善ループを、文書の手順から実行可能なローカル処理へ進める。既存のlocal task eventとstatus writerは観測を記録するが、改善候補の冪等な実行、試行予約、中断回復を持たない。Worldの状態やIssue #22のアプリ変更とは切り離す必要がある。

## 決定

`scripts/automation/runner.py`を明示起動する唯一のdispatcherとし、checkoutに固定したSQLiteへ1候補のpacket・予算・lease・状態遷移を保存する。全操作はOS lock下で行い、書込み前に試行を予約する。OS lockの解放とlease期限の両方を確認してから、中断作業を`unknown`へ回収する。結果不明の書込みは再送しない。

最初のrecipeは、AGENTSに新runbookの入口が欠けるという実在の問題を、既定の1行追加で直す。根拠fileと修正pathはcode上のallowlistに固定する。Issue本文やlogは指示として信頼せず、構造化session eventも時刻・種別の補助観測だけに使う。

試行・時間の保存上限と、外部API・費用0を強制する。承認待ちは保存して終了し、人間の回答を生成・転送する機能を置かない。OS常駐、外部credential、有料resource、公開、保護緩和、外部への自動投稿、Codex起動を含めない。

## トレードオフ

- JSON file群よりSQLiteのtransactionで予約・状態・履歴を一括確定しやすい。DBとfileの更新は単一transactionではないため、クラッシュ後はhash照合による読み取り回復と結果不明を区別する。
- leaseだけでのowner乗取りを避けるためOS lockも必要になる。単一checkoutだけを排他対象とし、複数PCや悪意ある同一権限プロセスの協調制御は扱わない。
- 固定recipeは改善範囲が小さい一方、任意promptやshellを実行するよりも権限、変更量、検証条件を明確にできる。一般的なコード改善や連続campaignは別課題で拡張する。
- 既存runbookのclaim未導入方針に対する例外は、このローカルrunnerの候補・dispatcher内部だけ。PM、Issue、他work itemのownerを自動claimしない。
- ローカル検査の成功と、課題全体の独立review、current-head check、CI、mergeを分離する。実行手順と停止状態は[runbook](../runbooks/self-improvement.md)を正典とする。
