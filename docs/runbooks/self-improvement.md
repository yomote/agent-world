# bounded local自律改善runner

明示起動したときだけ、許可済みの根拠データから候補packetを作り、固定したローカル修正を1件実行・検証する。Python 3.11以上の標準ライブラリと既存Gitだけを使う。World、Actor、Web、API生成物、既存wrapperとは独立している。

これはMind Inbox由来の改善ループをagent-worldの境界に縮めた最小実装である。既存Mind Inboxコードを直接コピーしたものではない。採否は以下のscout提供結果に記録する。設計理由は[ADR 0009](../adr/0009-bounded-local-improvement.md)、課題全体のreview・納品は[単一課題の完遂ループ](single-task-loop.md)を参照する。

## 実jobと通常GitHub統合

公開Codex CLIを起動する `jobs.py`、状態を所有する `delivery.py`、GitHub I/Oを直列で実行する `github_adapter.py` を使う。GitHubの実運用backendはこのnative REST/GraphQL adapterだけ。既知403のconnectorへ戻るfallbackはない。hostは `api.github.com`、repoは `yomote/agent-world`、method/path/operationはcodeの固定allowlistとする。workflow dispatchはallowlistにも実行経路にも含めない。

```powershell
python -m scripts.automation.delivery runner init --owner <thread-uuid> --base <base-sha>
python -m scripts.automation.delivery runner smoke
python -m scripts.automation.delivery runner deliver
```

`smoke`は実 `codex -a on-request exec --json --sandbox read-only` を起動し、thread・turn完了・exit code・markerを検証する。helperはworkspace-writeで固定3fileだけを実装する。CLIの承認要求に回答せず、merge・dispatch・公開・GitHub I/Oをcode jobへ渡さない。[公式non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)とPCの公開helpを契約にする。

GCMの既存managerを対話無効で利用し、認証応答はadapter process内のメモリでAuthorization headerにだけ使う。モデルへの返却、引数・環境変数・fileへのtoken出力、ログ出力、別credentialへのfallback、新規loginは行わない。Git objectのfetch/pushも同じadapterが既存Git/GCMで直列実行し、結果不明なら再送しない。

REST/GraphQLのHTTP予算はstore全体で30件。runner/helper、再起動、revision、ETagの304、失敗、unknown writeを同じSQLite counterへ合算する。送信前に予約し、31件目は未送信。予約後の失敗でも払い戻さない。redirect・自動retry・次page追跡は行わず、page欠落なら停止する。401/403は保存停止し、制限応答の待機headerを記録してその場で終了する。Git protocolの内部HTTP往復数はこのREST/GraphQL counterに含めない。

そのほかの強制境界はrunnerの保存期限2時間と、通常merge後にhelperを初期化した時点から新しく30分（明示`seconds=1800`）の絶対期限。helperの手動initも30分固定で、再開・修正・再initで延長しない。期限到達は保存停止する。CLI最大3起動・各900秒（smoke180秒）、local delivery-stageはcampaignごと最大40操作（保存fieldは`max_connector_calls`）、CIはheadあたり60秒以上・最大10照会。Git操作は30秒、HTTP socketは15秒、responseは4 MiB。CLI内部のモデル要求数と費用hardcapは未提供で、回数上限を費用保証と呼ばない。

helperはstore内でcampaignが1つ、固定findingと重複キーで候補1件、dispatcherのOS lockで同時jobは1つ。実装jobは重複キー保存後に1回だけ起動し、自動retryや再launchを提供しない。CLIの3起動上限は追加の天井であり、2回のretry機能があるという意味ではない。helperのreview→check→CI→mergeのdelivery試行は実行前に永続予約し、初回と明示修正後の最大2回、合計3回で停止する。中断reviewの再要求もこの回数を消費する。unknown write・承認待ちからの再送はない。30分・3試行のpacketを表せない旧helper stateも保存停止する。

`transport.py`はGitHub操作を予約してnative adapterを同期実行し、実応答をDBへ保存する。正式reviewとlocal checkだけは `relay.mjs` の `pump(root, campaign, tools)` が `bridge take` で一度claimする。reviewの固定packetを同じread-only reviewerへ渡し、実回答を `complete` で返す。checkは既存factoryの180秒timeoutでclean current headの `npm run check` を実行する。後付けの外部成功receiptをimportするcommandはない。

`deliver`は新固定headのreview→current check→push→Draft PR→review証跡→Ready→current CI→通常mergeを駆動する。CIは同じPR/head/workflowの最新runと実check jobのcompleted/successを必要にする。Draft skipを成功にしない。merge直前は保護、current PR/head、独立review、全threads、同headのCIとlocal checkを結合し、標準REST squashへexpected SHAを渡す。merge要求はcampaign/headにつき1回だけ予約する。hidden bypass設定は未検証として残し、保護/read権限を変更せず、merge-gate workflowやAzure dispatchを呼ばない。

review/check/CIの既知failは、cleanな子孫commitへ修正して `delivery <campaign> revise --workspace <path>`、続いてdeliverで再検証する。同じ予算とPRを維持する。approval_wait・unknown write・期限切れownerは停止を保持し、通常reviseでは解除しない。

今回受領したphase handoffだけは `runner handoff --reviewed-head d1baecb6cd85847ac6976e2cd68f277b80894a87` で適用する。旧headの正式review/checkと「GitHub write前に停止済み」というユーザー確認に一致する保存境界に限り、新headの再reviewへ進める。旧PASSを新headへ移さず、回数・期限をresetしない。一般的なunknown writeやHITLを解除する入口ではない。

runner通常merge後、実billing debriefの確認済み `debrief_id/finding/source_sha256/source_ref` から候補を作る。findingは `billing_evidence_normalization` 固定、duplicate keyは `sha256(debrief_id + ':billing-evidence-normalization-v1')`。Issue/log本文を指示にせず、helperの新規3fileだけを独立jobへ渡す。runner merge SHAの取得・main上の確認・新規path確認をIssue作成前に行う。実装SHA→同じ正式reviewer→current check/CI→通常merge→Issue完了を保存する。Azure runbook編集、追加Azure read、公開、credential変更は禁止する。

helperはAPI/version/JPYをhardcodeせず、妥当な入力通貨を返し、blank・異形で停止する。JPYは今回の確認証跡のみ。既存Azure runbookはAzure ownerが保持し、childはリンク追加も行わない。

```powershell
python -m scripts.automation.delivery billing-helper init --owner <thread-uuid> --base <runner-merge-sha>
python -m scripts.automation.delivery billing-helper helper --source <confirmed-debrief.json>
python -m scripts.automation.delivery billing-helper deliver --workspace <saved-checkout>
```

read-only review要求をclaimした後にdispatcherが停止した場合は、`resume-review --request-id <保存ID>`で再開可否を確認する。OS lockを取得でき、leaseが失効し、最後の未完了操作が同headの独立reviewである場合に限り旧要求をcancelled_read_onlyとして保存する。runnerはroot、helperは専用checkoutを要求packetのworkspaceと照合し、そのcheckoutのhead・祖先関係・scopeを検証する。旧要求の取消しと復帰状態は同一DB transactionで保存する。新要求で正式reviewを受け直し、予算は保持する。approval_wait・unknown writeはこの判定を通らない。

CLIのthread開始event欠落とturn完了event欠落は別の停止理由で保存する。各完了・停止にはoutcome、reason、head、job owner、PR、1文の学びだけの短いdebriefを残す。分散lease/CASや自動scope拡大は行わない。

## Mind Inboxからの採否

本体からhelperまで一周する実行は
`python -m scripts.automation.delivery runner deliver --helper-source artifacts/self-improvement/billing-source.json`。
runnerの通常merge成功後にだけ、同じdispatcherがhelper campaignを初期化し、実装job・review・check・CI・通常merge・Issue完了へ進む。既存venvは専用checkoutへ複製し、追加downloadやcredential設定をしない。

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
