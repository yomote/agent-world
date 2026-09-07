# bounded local自律改善runner

明示起動したときだけ、許可済みの根拠データから候補packetを作り、固定したローカル修正を1件実行・検証する。Python 3.11以上の標準ライブラリと既存Gitだけを使う。World、Actor、Web、API生成物、既存wrapperとは独立している。

これはMind Inbox由来の改善ループをagent-worldの境界に縮めた最小実装である。既存Mind Inboxコードを直接コピーしたものではない。採否は以下のscout提供結果に記録する。設計理由は[ADR 0009](../adr/0009-bounded-local-improvement.md)、課題全体のreview・納品は[単一課題の完遂ループ](single-task-loop.md)を参照する。

## 実jobと通常GitHub統合

追加の明示依頼により、旧pilotとは別のcampaignを同じSQLite・dispatcher lockへ追加した。`delivery.py`が状態遷移のowner、`jobs.py`が公開Codex CLI、`transport.py`と`bridge.py`が既存GitHub connectorへの配送を担う。runnerが操作を予約して要求を出し、生存中に一致する応答を受けて次へ進む。後付けの成功receiptをimportするcommandはない。

```powershell
python -m scripts.automation.delivery runner init --owner <thread-uuid> --base <base-sha>
python -m scripts.automation.delivery runner smoke
python -m scripts.automation.delivery runner deliver
```

`smoke`は実`codex -a on-request exec --json --sandbox read-only`を起動し、`thread.started`、`turn.completed`、exit code、固定markerを検証する。jobのログと構造化ownerを保存する。helper実装には`workspace-write`を使うが、auto approval、ignore-rules、full access、bypassは指定しない。保存済みCLI認証の通常利用に限り、credentialの取得・複製・新設はしない。[公式non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)と、そのPCの公開`--help`を契約として使う。

配送側は`python -m scripts.automation.bridge runner take`で一度だけ要求をclaimし、そのoperationを既存connectorまたは同じ正式reviewerへ渡す。`.sent.json`の排他的作成により配送側再起動でも同じwriteを再送しない。返却fileは`transport/<request-id>.response.json`、envelopeは`id/status/result`だけ。既存toolの実応答を渡し、`isError`、承認要求、結果不明を`ok`に書き換えない。runnerは要求ID、期限、owner fenceを照合する。配送側は信頼済みoperatorの実行環境であり、任意第三者へのHTTP endpointではない。

`deliver`は固定headへの独立review要求、`local_check.py`によるclean current headの`npm run check`、push、Draft PR、review証跡、Ready、current CI、merge直前の保護・head・review・threads再検査、通常squash mergeを順に駆動する。checkは既存`factory.run_command`のプロセス停止と180秒timeoutを再利用する。mergeは既存connectorの通常操作にexpected headを指定し、保護設定やread権限を変更しない。hidden bypass情報が欠けるworkflowを成功させるための保護緩和は行わず、そのworkflow自体は呼び出さない。`protected=true`と通常merge実応答を保存するが、hidden bypass設定を検証済みとは記録しない。Azure dispatchや追加公開は起動しない。

campaignの強制境界は、保存した2時間の全体期限、CLI最大3起動・各900秒（smokeは180秒）、connector配送最大40回、current CIはheadあたり60秒以上・最大10照会。再起動でbudgetをresetしない。Git操作は各30秒、入力とログにもサイズ上限がある。CLI内部のモデル要求数と費用hardcap、connector内部のHTTP回数hardcapは公開interfaceから提供されない。`cost_hardcap=not_provided`等で区別し、呼出回数上限を金額上限と呼ばない。

承認待ち・unknown write・期限切れownerは保存停止し、自動回答や再送をしない。CLIが未完了のまま停止した場合も成功を作らない。生存中dispatcherのlockを期限だけで奪わず、死んだownerの期限切れは`unknown`へ回収する。

runner本体の通常merge後、実billing debriefから確定した`debrief_id/finding/source_sha256/source_ref`だけをhelper入力にする。`finding`は固定の`billing_evidence_normalization`。本文や任意commandをpromptへ連結しない。重複keyは`sha256(debrief_id + ':billing-evidence-normalization-v1')`、scopeは`billing_debrief.py`、専用test、新しい短いhelper文書だけ。`helper`がIssue作成と独立checkout・実CLI jobを起動し、実装SHAを検証する。その後同じ`deliver`でreview・CI・通常merge・Issue完了を進める。実debriefの所在や事実を確認する前に、この入力をfixtureで代用しない。Azure runbook編集・追加Azure read・公開・credential変更は禁止する。

```powershell
python -m scripts.automation.delivery billing-helper init --owner <thread-uuid> --base <runner-merge-sha>
python -m scripts.automation.delivery billing-helper helper --source <confirmed-debrief.json>
python -m scripts.automation.delivery billing-helper deliver --workspace <saved-checkout>
```

## Mind Inboxからの採否

current CIはworkflowのsuccessだけでは受け入れず、対象runの`check` jobもcompleted/successであることを取得・保存する。Draftのjob skipは未検証のまま扱う。

配送の実行入口は`scripts/automation/relay.mjs`の`pump(root, campaign, tools)`。既存Codex tool hostで呼び、1要求のclaim、allowlistしたconnector呼出または実`local_check`、一致IDへの応答保存を実行する。認証情報を別processへ渡さない。`pending_review`だけは同じ正式read-only reviewerへ固定packetを渡し、その実回答を`complete(root, request, {id, status, result}, tools)`で返す。新たなreviewerや承認回答を自動生成しない。無要求ならnullで終了し、常駐pollerは持たない。

review/check/current CIの既知失敗を修正した場合は、cleanな子孫commitを作って`delivery <campaign> revise --workspace <path>`、続いて`deliver`を明示実行する。同じcampaignの期限・回数・既存PRを維持し、新headを同じreviewerから再検証する。approval_wait/unknownをこの入口で解除しない。CIはPR番号とheadとworkflow pathを照合し、ページ欠落・identity欠落では成功にしない。helperは保存したrunner merge SHAがmainに存在することと、新規3fileの空きを外部Issue作成前に確認する。

参照revisionは`d3c15275b50d3686dba226fd98a1bdffc581a8dd`。以下は別scoutの提供結果に基づく採用判断であり、このownerによる全資料の再調査や直接コピーではない。

| 参照元                                                          | 判断と理由                                                                                                                           |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `.claude/skills/dispatch/SKILL.md`                              | Issue・DoD・file境界・revision・報告先のpacketを採用。runnerの固定scope・owner・head・要求先へ縮約する                               |
| `cicd/scripts/review-gate/check.py`                             | 現head、独立review、threadsの再検査を採用。既存`merge_gate.py`のreview marker契約を再利用し、CIと通常mergeの実応答を記録する         |
| `cicd/scripts/status-page/watchers.json`                        | 期待間隔・trace marker・staleを採用。lease heartbeat、CLI marker、CI 60秒間隔、期限切れunknownへ対応。OS watcherは導入しない         |
| `docs/adr/archive/operations/autonomous-ux-improvement-loop.md` | 設計根拠のみ採用。実行コードや実証済み機能と同一視しない                                                                             |
| lease / CAS                                                     | 移植元も設計段階。今回の要件としてSQLite transactionと最小local single-dispatcher leaseを新規補完                                    |
| agent-world `scripts/factory.py`                                | 180秒、timeout時のprocess終了、実測reportを再利用                                                                                    |
| agent-world single-task-loop / merge_gate                       | 固定head、同じ独立reviewer、current証拠、通常保護merge、unknown write停止を維持。hidden bypassを解決するための権限・保護変更は不採用 |

## 旧pilot: 固定ローカル修正の起動と一周

リポジトリ直下で実行する。常駐登録、watch、timer、OS task、wakeはない。`init`で固定する予算はこのcheckoutの保存状態全体に適用し、毎回の起動では回復しない。期限内に`scan`と`run`を実行する。

```powershell
python scripts/automation/runner.py init --max-attempts 1 --max-seconds 120
$candidate = python scripts/automation/runner.py scan | ConvertFrom-Json
python scripts/automation/runner.py run $candidate.id --owner $env:CODEX_THREAD_ID
python scripts/automation/runner.py status
```

`--owner`は構造化session metadataで確認したUUIDを渡す。環境変数はそのUUIDと照合してから使う。`scan`が`no_candidate`なら`run`は不要である。初期化済みstoreへの`init`は拒否する。

旧pilotのrecipeは`agents-self-improvement-entry-v1`の1種類だけ。`docs/runbooks/self-improvement.md`が存在し、`AGENTS.md`にその入口が欠け、AGENTSがGit上cleanなら、既定の日本語1行を末尾へ追加する。本文の指示は解釈しない。既存内容と改行形式を保持し、期待する追記との差分、runbookのhashを検証する。既に入口があれば変更しない。この実装導入時の欠落を使って実checkoutで一周を実証し、その追記を成果commitへ含める。欠落状態を作り直すために入口を削除しない。

保存先はgit管理外の`artifacts/self-improvement/`に固定する。CLIに任意state path、任意修正path、shell command、Issue本文の入力口はない。1つのstoreが扱う候補は最大1件であり、既存候補があると再scanでも同じ候補を返す。予算停止後のscanは`stopped`を返す。候補IDはrecipe・根拠hash・期待結果から決まり、既存候補への再runは再書込みしない。

これは1件のpilot用の範囲である。次の改善を継続投入する機能、store削除による予算reset、自動retry、任意コード修正は提供しない。新しいrecipeやcampaignが必要な場合は、別の明示課題で対象・許可・予算を設計し、固定commitへの独立reviewを受ける。

## 旧pilotの状態・owner・回復

SQLiteが候補packet、状態、試行予約、owner UUID、lease、検証結果、短いevent履歴をtransactionで保存する。CLIの全操作は同じ固定OS file lockを保持する。別dispatcherは待機ループを作らず終了する。OSはプロセス終了時にlockを解放する。lock fileは削除しない。

| 状態            | 意味と次の操作                                                                       |
| --------------- | ------------------------------------------------------------------------------------ |
| `ready`         | 固定recipeの候補を保存済み。ownerを指定して`run`する                                 |
| `running`       | 試行を先に予約済み。owner・token・最大30秒のleaseを保持する                          |
| `completed`     | 当該ローカル修正のpostconditionを検証済み。Issue完了、review合格、CI成功を意味しない |
| `approval_wait` | 人間判断、dirty targetまたは権限拒否で保存停止。runしても再試行しない                |
| `unknown`       | 中断、期限切れまたは操作結果不明。書込みを再送しない                                 |
| `stopped`       | 予算、時計巻戻り、根拠変更で停止。起動し直しても再開しない                           |
| `failed`        | 書込み後のpostcondition検査に不合格。自動修復や巻戻しをしない                        |

```powershell
# 必要な人間判断を保存して停止する。回答・承認の代行ではない。
python scripts/automation/runner.py hold <candidate-id>

# OS lockを取得でき、保存leaseも切れた場合のみunknownへ回収する。
python scripts/automation/runner.py recover <candidate-id>

# unknownについて、期待する修正結果が既に存在するか読み取りだけで照合する。
python scripts/automation/runner.py reconcile <candidate-id>
```

生存中のdispatcherから期限だけでownerを奪わない。回収時は旧ownerと試行数を残す。`reconcile`は対象fileとrunbookのhashが保存packetに一致した場合だけ、クラッシュ前の修正が存在すると記録する。修正前のまま、別担当変更、または証拠不足なら`unknown`を維持する。中断で残った`.self-improvement-*`一時fileを自動適用しない。

`approval_wait`は永続的な保存停止であり、このCLIには`approve`、承認token入力、自動回答、権限設定変更、承認待ちからのresumeがない。人間が必要な判断を本来の窓口で行うまで、当該作業を停止したまま引き渡す。許可不足や拒否を別経路で迂回しない。

## 旧pilotの予算と入力境界

- 試行上限は1〜3、時間は1〜300秒。既定は1試行・120秒。現recipeは再試行を提供せず最大1回だけ実行する。試行を実行前に永続予約し、失敗・中断でも払い戻さない。
- 保存した絶対期限と起動中のmonotonic clockで、書込み直前・直後にも確認する。時計が保存時刻より巻き戻った場合は停止する。Git照会には最大5秒か残り時間の短い方を設定する。小さい同期I/O自体をOSから強制中断するwall-clock保証ではない。
- API回数と費用枠は0固定。`--max-api-calls`、`--max-cost-microusd`に0以外を渡すと拒否する。ネットワーク、LLM、Codex子起動、外部credential、有料resource、公開、push、PR、merge、保護緩和を行う実装はない。これは外部利用枠の値であり、PC消費電力などの実測費用ではない。
- 根拠はallowlistした2つのfileのhashと、入口の欠落という機械判定のみ。候補のpath、recipe、期待結果を実行時に再照合する。Issue本文、log本文、tool引数、会話、review文言を実行指示に変換しない。
- fileは各128 KiBまで。symlink、Windows reparse point、hardlinkを拒否する。scan後の対象・runbook変更とAGENTSのdirty stateを拒否し、他ownerの差分を上書きしない。
- SQLiteとcheckoutはローカルownerが保有する状態であり、悪意ある同一権限プロセスに対する隔離sandboxではない。同じfileを編集する他ownerとは従来通り事前調整する。別checkoutは独立したstoreを持つ。

## 既存local eventとstatusへの接続

既存の`scripts/sync_status_from_local_events.py`が使う`session_meta`と`event_msg`の構造に接続する。既存collectorのchild mappingとは別に、明示された1つのsession fileと正確なowner UUIDを照合するため、task-primaryの観測にも使える。

```powershell
python scripts/automation/runner.py observe --owner <structured-thread-uuid> --event-file <session.jsonl>
python scripts/automation/runner.py status
```

最初の2行にある`session_meta.id`をthread UUIDとして照合する。`id`がない形式だけ`session_id`を使う。childの`session_id`は親と共有される場合があるため、`id`が存在すれば代用しない。許可した`task_started` / `task_complete`の時刻・種別だけを抽出し、本文、path、secret、token usageは保存しない。読取は16 MiB・10万行、1行1 MiBが上限。未終端の行は次回の明示読取まで確定しない。これは発火条件ではなく補助観測であり、`task_complete`を改善成功、承認、Issue完了には変換しない。

`status`のJSONには候補・lease・検証・stop reason・履歴と最新の補助観測がある。既存UIの`artifacts/status/current.json`は上書きせず、sourceを`pm-confirmed`や`codex-event`と偽装しない。既存の管理画面の契約は[local status](live-status.md)に従う。画面連携とApp Server接続はこの課題では未実装。

終了codeは通常0、保存停止・結果不明・失敗・入力エラーは2。`status`は保存状態の読取なので、停止済みでも0でJSONを返す。必ず`state`、`reason`、`verification`を併せて確認する。例外の生本文は秘密や入力文言の混入を避けて表示しない。

## 検証と納品証跡

```powershell
python scripts/python_env.py -m pytest scripts/tests/test_self_improvement.py
npm run check
```

専用testは冪等性、保存した予算、単一dispatcher、実プロセス終了後のlock解放、lease回復、承認待ち、権限拒否、根拠改変、本文混入、読取制限を検証する。実証の`status` JSONと実行結果は`artifacts/self-improvement/`へ保存し、PR等の受渡しには対象commit、独立review、current headのcheck結果、未検証範囲を記録する。runnerの`verification=pass`はその修正だけの局所検査であり、`npm run check`や独立reviewの代わりにはならない。
