# 0009. 明示起動するbounded local改善runner

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

## 公開CLI・通常GitHub adapterへの拡張

受領したphase handoffにより、GitHub backendは既存GCMをprocess内で使う単一REST/GraphQL adapterへ固定した。connector fallbackとworkflow dispatchは実行経路から除外する。HTTP予約counterはstore全体30件で、再起動・helper開始でもresetせず、31件目を送信しない。独立review・current check・CI・PR/head・保護・threadsを結合し、通常REST squashのexpected SHAを1回だけ送る。結果不明は再送しない。code jobはGitHub I/Oを所有しない。

追加依頼により、公開Codex CLI jobとnative GitHub adapterを使うcampaignを追加する。旧pilotの保存状態をresetせず、同一dispatcherが操作予約→要求発行→実応答検査→次状態を所有する。transportは操作の配送だけを担い、過去の外部結果から任意stateを後付けしない。費用とCLI内部モデル要求のhardcapは未提供と記録し、時間・起動回数・配送回数の強制境界と分離する。

scoutの採用元はrunbookへ集約する。移植元のlease/CASは設計段階のため、この実装の出典として実証済みを主張しない。現headのreview/CI/threadsを再検査して通常mergeし、hidden bypass不明のworkflow成功、Azure公開、保護/read権限変更へ範囲を広げない。
