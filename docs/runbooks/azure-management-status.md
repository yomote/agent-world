# Azure管理status 初回公開packet

## 推奨する1案

管理statusをWorldアプリと別の`rg-agent-world-mgmt-jpe`へ置く。Azure Container Apps Consumptionで既存FastAPI/UIを動かし、最新snapshotは非公開Blob 1件だけを保持する。本人のbrowser GETと専用ingest identityのPUTをsingle-tenant Entra + Easy Auth + backend OID検査で分離する。

このpacketは実装・ローカル検証・独立reviewまでを承認前に終える。Azure、Entra、GHCR、OIDC、credential、Internet公開のwriteは本人の明示承認後に行う。

## 依存と承認対象

local statusの[PR #16](https://github.com/yomote/agent-world/pull/16)は通常squash merge済みで、依存先mainは`910952672fcfef1e2e0626a402e51646b9d5fe1c`に固定する。[PR #18](https://github.com/yomote/agent-world/pull/18)のAzureコミットだけをこのmainへ移す。旧base `f098727094ca49f2471d7245143a447c87829c0a`とmainのtreeは一致し、`e2967f4`から移した`c0a808c`のrange-diffも一致した。この文書更新はその後の独立した差分である。最終review対象の40文字SHA、child ID、検証とCIはPRのcurrent-head証跡を正本とする。

公開の依存順は、#16の固定main → PR #18のreview・CI・別途許可されたmerge → 両差分を含むmainの40文字SHA固定 → image build/smoke → immutable registry digest確定 → 本人の公開承認 → 匿名pull → what-if → plan/parameter承認 → applyである。このunitはmain mergeも以下のlive操作も実行しない。

registry digestを得るための非公開GHCRへのpushと、package public化・Azure公開は別々に承認する。既存workflowの`publish=false`はdigestを生成しないため、それだけでimage確定済みとは扱わない。`publish=true`は同じmain SHAを再buildしてsmokeするので、そのrunで実際にpushしたdigestとrun URLを採用し、先行dry runのimageと同一だとは推定しない。事前に対象packageが非公開であることを確認できない場合は、digest確定用のpushを開始しない。

| 承認記録   | 現在の状態と必要な証拠                                                                                                                                                           |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| source     | #16/mainは上記SHAで確定。PR #18 merge後のmain SHAは未取得。PR review SHAとの対応、差分、CIを記録してからimage workflowの`expected_head`に渡す                                    |
| image      | build/smoke run、非公開GHCRへの1回pushの許可、実際の`@sha256:` digestは未取得。package public化の明示承認と、そのdigestの匿名pull成功日時も未取得                                |
| 請求と通知 | 実請求通貨のread-only出典・確認日時、本人確認済み通知メール・確認日時は未取得。既存候補値を確認済み実値へ読み替えない                                                            |
| identity   | subscription / tenant / deploy本人OID / API app client ID / ingest service principal OIDの実照合は未実施。CLIのsigned-in user OIDと`OperatorObjectId`の一致をwhat-if前に確認する |
| deployment | Core / Protectedごとのhead、全parameter、confirmation hash、保存planとplan hash、本人の明示承認は未取得。phase間で承認を流用しない                                               |
| rollback   | 下記回収手順を公開承認に含める。初回は直前の稼働digestがないため、認証失敗時はexternal ingress無効化とpublisher停止を選ぶ。RG削除・Key Vault purgeは別承認                       |

未取得値は空欄のまま承認対象から外すのではなく、該当するlive操作の停止条件とする。承認者・確認時刻・対象操作をPRの承認記録へ残す。credential値やprivate keyは記録しない。

## 固定候補

| 項目               | 値・境界                                                                                               |
| ------------------ | ------------------------------------------------------------------------------------------------------ |
| Subscription       | 既存確認済み候補`omote-dev-subscription` 1件。承認後にID、tenant、signed-in userを再照合               |
| Region             | Japan East (`japaneast`)                                                                               |
| 管理Resource Group | `rg-agent-world-mgmt-jpe`。World用`rg-agent-world-jpe`を変更しない                                     |
| Container App      | `agent-world-status-yomote-jpe`候補。衝突時は停止し、別名を勝手に採用しない                            |
| Compute            | Consumption、0.25 vCPU / 0.5 GiB、min 0 / max 1、worker 1、single active revision                      |
| Storage            | StorageV2 Standard_LRS、container `status`、Blob `current.json`、匿名/shared key/履歴なし              |
| Logs               | Log Analytics PerGB2018、30日保持、0.023 GB/日cap                                                      |
| Key Vault          | Standard、RBAC、purge protection、soft delete 7日。Easy Auth secretだけ                                |
| Budget             | 実通貨JPY確認後に月1,000円。50% actual / 80% forecast / 100% actualを確認済みメールへ通知              |
| Image              | `ghcr.io/yomote/agent-world-status@sha256:<digest>`。配布対象はstatus backend/UIと固定requirementsだけ |
| 公開境界           | `/healthz`だけ匿名。UI、GET API、PUT APIはHTTPS + Entra必須                                            |

Budgetは通知であり支出を止めるhard capではない。Log Analytics cap、min 0、max 1も総費用を保証しない。Container Appsの月間free grantはsubscription内の他利用と共有され、Storage、Key Vault、Log Analytics、network、requestは条件に応じ課金される。請求通貨と実単価は未確認であり、Cost API 429で使い切った照会をこのunitでは再試行しない。

このPCではDocker clientを確認できないため、imageの実buildはまだ完了していない。承認後の具体経路はmain上の手動`Management status image` workflowである。review済み40文字headとの一致を検査し、GitHub-hosted runnerでbuildと`/healthz` smokeを行い、`publish=true`の1回だけ既存`GITHUB_TOKEN`（image jobに限定した`packages:write`）でGHCRへpushしてdigestを記録する。新しいPATと有料Azure resourceは不要である。GHCR packageのpublic化にはrepository/package admin権限と本人承認が必要で、匿名pullを確認できない場合はCore what-ifへ進まない。GitHub Actions利用枠とpackageの実設定は未確認で、利用料金または枠を確認できない場合もdispatchしない。

workflowのpush結果が不明なら同じheadを自動または手動で再dispatchしない。GHCRのpackage/versionをread-onlyで一度照合し、digestを確定できなければ停止する。build/smokeだけの`publish=false`と外部writeを行う`publish=true`、package public化は別の操作として承認・記録する。

## identity・credential・permission manifest

| identity                             | credential                                      | 許可                                                                    |
| ------------------------------------ | ----------------------------------------------- | ----------------------------------------------------------------------- |
| deploy operator本人user OID 1件      | 既存Entra login                                 | UIとGET `/api/status`。Key Vault secret rotation                        |
| status API app / service principal   | 1年client secretを承認後に作りKey Vaultへ直送   | Easy Authのsingle-tenant resource app                                   |
| ingest app / service principal       | 承認後にlocal保管のcertificate public keyを登録 | `Status.Ingest` app roleとPUT `/api/status`だけ。UI/GET、Azure RBACなし |
| Container App user-assigned identity | credentialなし                                  | Blob container `status`のData Contributor、Key Vault Secrets User       |

API app roleの正本は`infra/azure-status/api-app-roles.json`。Easy Authのallowed principalsは本人OIDとingest service principal OIDだけ。platformが認証済みrequestへ付与する`X-MS-CLIENT-PRINCIPAL-ID`をbackendでroute別に照合する。App Server、session log、local mappingを公開しない。

local publisherはAzure CLIが承認済みingest service principalでlogin中であることを確認してtokenを得る。新しい`local-event-record`だけを一度PUTし、応答不明では`artifacts/status/publish-state.json`を`unknown`にして停止する。同じpayloadも後続payloadも自動送信しない。本人が管理画面またはBlob actualを確認し、明示的な回収判断をするまでstateを消さない。

承認後の明示起動commandは次の形にする。`api://<auth-client-id>`は承認済みAPI appのaudienceであり、publisherはAzure CLIのlogin主体が`<ingest-client-id>`と一致しない場合に停止する。

```powershell
python scripts/python_env.py scripts/publish_status_snapshot.py `
  --base-url https://<management-status-fqdn> `
  --audience api://<auth-client-id> `
  --ingest-client-id <ingest-client-id> `
  --watch
```

## what-ifの許容差分

`scripts/azure-status/Deploy-ManagementStatus.ps1`はplanを`artifacts/azure-status/what-if.json`へ保存する。Azure CLIはこのsessionのPATH外だが、既存CLIを絶対pathで使えることを確認した。Azure接続を伴うwhat-ifは**未実行**である。

scriptはreview済み40文字headが現在のcheckoutと一致することを検査する。Azure CLIの主体はuserだけを許可し、`az ad signed-in-user show`のobject IDがdeploy operatorの`OperatorObjectId`と一致しなければwhat-if前に停止する。実請求通貨のread-only確認結果を識別する`CostConfirmationSource`、確認日時、本人が通知先を確認した日時と明示switchを必須にし、subscription、tenant、resource名、image digest、Budget値、Entra IDを含むconfirmation recordを表示する。what-if SHA-256とconfirmation SHA-256の両方を本人が同じ承認packetで確認し、apply時に渡した値と一致しなければ停止する。Cost API 429を再試行して証跡を作らず、Azure portal等の本人が実際に確認できたread-only出典を記録する。

`OperatorObjectId`はAzure deploymentを実行するuserとUIを読むuserのOIDであり、HITLの承認行為そのものを表すIDではない。confirmation switchとhashは入力・差分の取り違えを防ぐgateで、本人による外部writeの明示承認を代替しない。

- `Core`: 対象subscriptionの専用RGと、templateで列挙したそのRG配下のresource IDだけを許可する。Create / NoChange以外、別subscription / RG / resource種別、既存RG利用で停止する。
- `Protected`: 同じ厳密なCore resource ID、対象RG・対象Appの`authConfigs/current`だけを許可する。対象Container AppのModify、auth configのCreate、NoChange以外で停止する。
- applyは同じ引数のwhat-ifが許容内で、保存planと表示されたplan / confirmation SHA-256を本人が確認し、`-Apply -ApproveReviewedPlan -ApprovedPlanSha256 <plan-hash> -ApprovedConfirmationSha256 <confirmation-hash>`を同時指定した1回だけ。再取得したwhat-if、head、parameter、確認証跡のhashが違えば停止する。
- write結果不明では再送しない。deployment operationとactualを読んで回収する。

## 承認前停止条件

次のいずれかに該当する場合はapplyを開始しない。

1. 独立review済み変更を含む承認済みmain headと、実build/smoke runおよび採用digestの対応を確認できない。
2. 実請求通貨がJPYと確認できない、確認日時・read-only出典を記録できない、またはBudget通知メールを本人が確認していない。
3. subscription / tenant / signed-in本人OID、Japan East利用可否、候補名の非衝突を再確認できない。
4. Core what-ifがCreate / NoChange以外を含む。
5. image匿名pull、配布内容限定、脆弱性確認を完了できない。
6. Entra application作成、admin consent、credential登録を行える本人権限がない。
7. GitHub Actions利用枠・料金、GHCRへのpackage write権限、package public化権限を確認できない。

未回答はBudget通知先メールであり、実請求通貨も未確認である。通貨がJPY以外、請求通貨を確認できない、候補名が衝突する場合は停止し、金額・名前・Budgetなし公開へ自動変更しない。Cost APIは429となった同じ照会をこのunitで再試行しない。

## 承認後の順序

0. #16は上記mainへ統合済み。PR #18のAzure-only差分、current-head reviewとCIを確認し、別途許可された担当が通常の保護条件でmergeする。merge後のmain SHAとreview済み差分の対応を確認し、imageとdeploymentに使う40文字SHAを改めて固定する。branch protectionを緩和せず、権限bypassを使わない。
1. 両差分が入った承認済みmain headで、手動`Management status image` workflowを同じ40文字head、`publish=false`で実行し、buildとhealth smokeを確認する。Actions利用枠・料金と非公開GHCR packageを確認し、非公開pushの本人承認後に1回だけ`publish=true`を実行する。このrunのbuild/smokeとimmutable digestを記録し、実請求通貨の証拠・時刻、通知メール本人確認、identity照合予定、rollbackを含む具体的packetでpackage public化・Azure公開の本人承認を得る。その後にpackageをpublicにし、同じdigestの匿名pullを確認してからwhat-ifへ進む。途中の値や結果が不明なら停止する。
2. 公開承認用のAzure context、region、候補名、請求通貨、当月costをread-onlyで確認する。これらの確認は手順1のpublic化承認より前に揃える。既に429で上限に達したCost API照会は再試行せず、本人が確認したportal等の出典と日時を使う。本人が確認したBudget通知先もscript parameterへ渡し、confirmation recordを承認packetへ添付する。
3. Core what-ifを実行し、専用RGのCreateだけをreviewする。同じ引数でCore applyを1回行う。この時点はinternal ingressである。
4. API app / service principalを作り、callbackを`https://<fqdn>/.auth/login/aad/callback`、single tenant、`Status.Ingest` roleにする。本人OIDを記録する。
5. API appの1年secretを作り、値を表示・file保存せずKey Vaultへ直送する。失敗時はkey IDでcredentialを削除する。
6. ingest app / service principalを作り、承認済みpublic certificateを登録し、API appの`Status.Ingest`をadmin consentする。private keyはlocalから出さない。
7. 作成直後にAPI app object ID / client ID、API service principal ID、secret key ID、ingest app object ID / client ID、ingest service principal ID、certificate key ID、app role assignment IDをlocal recovery journalへ保存する。secret値は保存しない。
8. Protected what-ifでContainer App Modifyとauth config Createだけを確認してapplyする。未認証UI/API拒否、本人login、ingest PUT、ingest identityのGET拒否、本人のPUT拒否を検査する。
9. local collectorとpublisherを明示commandで起動する。OS service / startupへ登録しない。新event、PC sleep、reader停止、unknown writeを確認する。
10. 本人がスマートフォンからloginし、source、最終受信、元の観測、stale / unknown、Issue / PR linkを確認する。

## 途中失敗の回収とrollback

- Core apply失敗: 自動再applyしない。subscription deployment operationと専用RG actualを取得し、作成済みresourceをjournalへ記録する。RG削除は別の破壊承認にする。
- API secret作成後、Key Vault保存前に失敗: responseのkey IDで`removePassword`を1回行う。結果不明ならcredential listをread-onlyで照合する。
- Entra途中失敗: recovery journalのIDだけでapp role assignment、service principal、applicationの逆順に回収する。識別できないobjectを名前だけで削除しない。
- Protected applyまたは認証smoke失敗: まずexternal ingressを無効化し、Internetから到達不能にする。auth修復まで公開完了としない。
- application回帰: 直前に確認済みのimage digestへ1回更新し、本人GET / ingest PUT分離とstale表示を再検査する。
- publisher応答不明: 自動再送しない。本人GETかBlob actualで`observed_at`を照合し、確認不能なら停止を継続する。

Blob `current.json`は最新1件だけで、履歴・local session recordは保存しない。Resource Group削除とKey Vault purgeは通常rollbackに含めない。

## 現在の検証状態

- local event readerとlocal同一origin UI/API: 実event追記で自動更新済み。
- Azure Blob store、route別auth、publisher unknown停止: local unit test対象。
- Python 3.11 local run: `/healthz`と実`local-event-record` snapshotのGET成功。Azure authはunit testで検査。
- container build / local run: 標準pathにDocker clientがなく未実行。未導入かdaemon停止かは断定しない。代替の手動GitHub Actions workflowは実装済みだが未実行で、Actions利用枠・料金、GHCR write権限、GHCR push、package public化、匿名pullは未確認。
- Bicep build: local Bicep CLI 0.46.1で成功。Azure接続を伴うwhat-ifは未実行。
- Azure / Entra / GHCR / credential / Internet公開 / smartphone actual: 未実行。
