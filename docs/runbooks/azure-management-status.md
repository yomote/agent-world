# Azure管理status 初回公開packet

## 推奨する1案

管理statusをWorldアプリと別の`rg-agent-world-mgmt-jpe`へ置く。Azure Container Apps Consumptionで既存FastAPI/UIを動かし、最新snapshotは非公開Blob 1件だけを保持する。本人のbrowser GETと専用ingest identityのPUTをsingle-tenant Entra + Easy Auth + backend OID検査で分離する。

このpacketは実装・ローカル検証・独立reviewまでを承認前に終える。Azure、Entra、GHCR、OIDC、credential、Internet公開のwriteは本人の明示承認後に行う。

## 依存と承認対象

local statusの[PR #16](https://github.com/yomote/agent-world/pull/16)とAzure準備の[PR #18](https://github.com/yomote/agent-world/pull/18)はmainへ統合済みで、この文書更新の基準は`origin/main 38a9858`である。`f00a76594371cc95d380e529fe3de775b87e9c94`は統合前のPR #18のreview済みheadであり、新しい作業branchへ旧Azure実装を再導入しない。コードのmain統合はAzure公開済みを意味しない。最終review対象の40文字SHA、child ID、検証とCIはPRのcurrent-head証跡を正本とする。

公開の依存順は、統合済みmainコード → 公開対象mainの40文字SHA固定 → image build/smoke → immutable registry digest確定 → 本人の公開承認 → 匿名pull → what-if → plan/parameter承認 → applyである。請求通貨JPYの証跡は確認済みだが、通知メールの本人確認とphase別plan承認は未完了である。この文書更新では追加Billing/Cost read、main merge、以下のlive操作を実行しない。

registry digestを得るための非公開GHCRへのpushと、package public化・Azure公開は別々に承認する。既存workflowの`publish=false`はdigestを生成しないため、それだけでimage確定済みとは扱わない。`publish=true`は同じmain SHAを再buildしてsmokeするので、そのrunで実際にpushしたdigestとrun URLを採用し、先行dry runのimageと同一だとは推定しない。事前に対象packageが非公開であることを確認できない場合は、digest確定用のpushを開始しない。

| 承認記録   | 現在の状態と必要な証拠                                                                                                                                     |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| source     | PR #18のコードはmain `38a9858`へ統合済み。公開に使うmainの40文字SHA、review済み差分との対応、CIを記録し、本人承認後にimage workflowの`expected_head`に渡す |
| image      | build/smoke run、非公開GHCRへの1回pushの許可、実際の`@sha256:` digestは未取得。package public化の明示承認と、そのdigestの匿名pull成功日時も未取得          |
| 請求と通知 | 実請求通貨JPY、subscription対応根拠、BillingProperty出典・UTC確認時刻は下記のとおり確認済み。通知メールと本人確認日時は未回答                              |
| identity   | 配備時のsubscription / tenant / 本人OID / API client ID / ingest OID照合は未実施。CLIのsigned-in user OIDと`OperatorObjectId`の一致をwhat-if前に確認する   |
| deployment | Core / Protectedごとのhead、全parameter、confirmation hash、保存planとplan hash、本人の明示承認は未取得。phase間で承認を流用しない                         |
| rollback   | 下記回収手順を公開承認に含める。初回は直前の稼働digestがないため、認証失敗時はexternal ingress無効化とpublisher停止を選ぶ。RG削除・Key Vault purgeは別承認 |

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
| Budget             | JPY確認済み。月1,000円。通知50% actual / 80% forecast / 100% actual（通知メール・plan承認待ち）        |
| Image              | `ghcr.io/yomote/agent-world-status@sha256:<digest>`。配布対象はstatus backend/UIと固定requirementsだけ |
| 公開境界           | `/healthz`だけ匿名。UI、GET API、PUT APIはHTTPS + Entra必須                                            |

Budgetは通知であり支出を止めるhard capではない。Log Analytics cap、min 0、max 1も総費用を保証しない。Container Appsの月間free grantはsubscription内の他利用と共有され、Storage、Key Vault、Log Analytics、network、requestは条件に応じ課金される。請求通貨JPYは確認済みだが、実単価・当月costの確認結果ではない。Cost API 429で使い切った照会をこのunitでは再試行しない。

## 請求通貨の確認結果

次は受領した確定済みのread-only確認結果であり、この文書更新で追加照会した結果ではない。

| 項目                 | 確認結果                                                                                                                                                      |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| currency             | `JPY`                                                                                                                                                         |
| subscription対応根拠 | `omote-dev-subscription`のsubscription IDを既存Azure CLI profileから解決し、その同一subscriptionの`BillingProperty default`を照会                             |
| source種別           | Azure REST BillingProperty API `2024-04-01` の `properties.billingCurrency`                                                                                   |
| endpoint             | `GET /subscriptions/{subscriptionId}/providers/Microsoft.Billing/billingProperty/default?api-version=2024-04-01`。`subscriptionId`は上記profileから解決したID |
| UTC確認時刻          | `2026-09-07T15:09:46.8412110Z`                                                                                                                                |

この出典を`CostConfirmationSource`、上記UTC時刻を`CostConfirmedAt`、`JPY`を`ConfirmedBillingCurrency`へ対応付ける。JPY確認は通知先メールの本人確認、Azure実行identityの照合、公開やplanの承認を代替しない。通知メール未回答とCore / Protectedそれぞれのhead・全parameter・plan hash・confirmation hashの承認待ちは維持する。

このPCではDocker clientを確認できないため、imageの実buildはまだ完了していない。承認後の具体経路はmain上の手動`Management status image` workflowである。review済み40文字headとの一致を検査し、GitHub-hosted runnerでbuildと`/healthz` smokeを行い、`publish=true`の1回だけ既存`GITHUB_TOKEN`（image jobに限定した`packages:write`）でGHCRへpushしてdigestを記録する。新しいPATと有料Azure resourceは不要である。GHCR packageのpublic化にはrepository/package admin権限と本人承認が必要で、匿名pullを確認できない場合はCore what-ifへ進まない。GitHub Actions利用枠とpackageの実設定は未確認で、利用料金または枠を確認できない場合もdispatchしない。

workflowのpush結果が不明なら同じheadを自動または手動で再dispatchしない。GHCRのpackage/versionをread-onlyで一度照合し、digestを確定できなければ停止する。build/smokeだけの`publish=false`と外部writeを行う`publish=true`、package public化は別の操作として承認・記録する。

## identity・credential・permission manifest

| identity                             | credential                                      | 許可                                                                                          |
| ------------------------------------ | ----------------------------------------------- | --------------------------------------------------------------------------------------------- |
| deploy operator本人user OID 1件      | 既存Entra login                                 | UIとGET `/api/status`。Key Vault secret rotation                                              |
| status API app / service principal   | 1年client secretを承認後に作りKey Vaultへ直送   | Easy Authのsingle-tenant resource app                                                         |
| ingest app / service principal       | 承認後にlocal保管のcertificate public keyを登録 | `Status.Ingest` app roleとPUT `/api/status`・`/api/status/upsert`だけ。UI/GET、Azure RBACなし |
| Container App user-assigned identity | credentialなし                                  | Blob container `status`のData Contributor、Key Vault Secrets User                             |

API app roleの正本は`infra/azure-status/api-app-roles.json`。Easy Authのallowed principalsは本人OIDとingest service principal OIDだけ。platformが認証済みrequestへ付与する`X-MS-CLIENT-PRINCIPAL-ID`をbackendでroute別に照合する。App Server、session log、local mappingを公開しない。

deploymentのGUID入力は小文字の標準表記へ正規化してparameterとconfirmation recordに使う。backendもOIDをGUIDとして比較するため、同じOIDの大文字・小文字の差では拒否しない。空欄・不正GUID・別identityは拒否する。

local publisherはAzure CLIが承認済みingest service principalでlogin中であることを確認してtokenを得る。新しい`local-event-record`だけを一度PUTし、応答不明では`artifacts/status/publish-state.json`を`unknown`にして停止する。同じpayloadも後続payloadも自動送信しない。本人が管理画面またはBlob actualを確認し、明示的な回収判断をするまでstateを消さない。

既存snapshot全体をingestへ読ませず一部だけ更新する場合は`PUT /api/status/upsert`を使う。serverはmanaged identityで現在Blobを読み、requestで指定した`agent` rowだけを置換または追加し、未指定rowを保持する。`runtime_capacity`もrequestに非null値がある場合だけ置換する。full PUTと部分更新の双方は同じBlob ETagを条件に1回だけ書き、競合は409で停止する。receiptは今回の保存対象とopaque revisionだけに限定する。ingestのUI/GET拒否、本人のPUT拒否、Easy Authの公開境界は変えない。

部分更新後のfull PUTは既存agentを省略できない。暗黙削除、itemの状態・activity・公開メモまたはcapacityについて同一clockの異なる内容、clockの退行やnonnullからnullへの消去は409にする。received_atやsnapshot全体のobserved_atだけを進めても、この競合を新しい内容とは扱わない。明示削除は別の契約が必要で、このendpointには追加しない。

部分更新の通信失敗や503では保存結果を判別できないため、receiptが得られなければcallerは`unknown`として同じrequestも後続requestも再送しない。409は未初期化、古い対象観測、またはCAS競合として明示的に停止する。全snapshotのGET権限をingestへ追加して回収せず、operator確認または別の承認済み回収手順を待つ。

送信前に保存した`attempting`がprocess/PC停止後に残った場合も未解決writeとして扱う。再起動後はtoken取得・同じsnapshotの再送・新しいsnapshotの送信をすべて停止し、本人のactual確認と回収判断を待つ。

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

実請求通貨JPYの出典・UTC時刻は確認済みであり、Budget通知先メールと本人確認日時が未回答である。phase別plan承認も待機中である。対象subscriptionが証跡と対応しない、通貨がJPY以外、候補名が衝突する場合は停止し、金額・名前・Budgetなし公開へ自動変更しない。Cost APIは429となった同じ照会をこのunitで再試行しない。

## 承認後の順序

0. #16と#18のコードはmainへ統合済み。公開に使うmain SHAとreview済み差分の対応・CIを確認し、imageとdeploymentに使う40文字SHAを本人承認の対象へ固定する。branch protectionを緩和せず、権限bypassを使わない。
1. 確認済みJPYのsource・UTC時刻を利用し、未回答のBudget通知先メールと本人確認日時を揃える。Azure context、region、候補名、実行identityの照合結果も公開前に揃え、confirmation recordを承認packetへ添付する。この文書更新ではBilling/Cost readを追加せず、JPYだけから実単価や当月costを推定しない。
2. 承認済みmain headで、手動`Management status image` workflowを同じ40文字head、`publish=false`で実行し、buildとhealth smokeを確認する。Actions利用枠・料金と非公開GHCR packageを確認し、非公開pushの本人承認後に1回だけ`publish=true`を実行する。このrunのbuild/smokeとimmutable digestを記録し、JPYの証拠・時刻、通知メール本人確認、identity照合、rollbackを含む具体的packetでpackage public化・Azure公開の本人承認を得る。その後にpackageをpublicにし、同じdigestの匿名pullを確認してからwhat-ifへ進む。途中の値や結果が不明なら停止する。
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

## 公開critical pathの4区分

| 区分         | 現在の事実                                                                                                                                                                                                                       | 公開へ進むために残るもの                                                                                                                           |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| mainコード   | #16 / #18はmain `38a9858`へ統合済み。status API/UI、Blob store、route別OID検査、publisherのunknown/attempting停止、Bicep、手動image workflowのコードがある                                                                       | 公開対象mainの40文字SHAとreview・CIの対応を固定する。古いPR headの実装を新branchへ重複させない                                                     |
| ローカル検証 | 旧head `f00a765`でtargeted pytest 22件、変更Pythonのlint/format、PowerShell AST/GUID正規化、文書整形が成功。既存のlocal event追記・同一origin UI/API、Python 3.11 HTTP確認、Bicep 0.46.1 buildの証跡がある                       | 証跡は各対象headの範囲に限定する。Docker build/runは未実行であり、コードの存在やunit testからcontainer実動作を成功扱いしない                       |
| 実環境検証   | BillingPropertyの`properties.billingCurrency=JPY`とsubscription対応・UTC時刻は上記のとおり確認済み。image workflow、GHCR push/public化/匿名pull、Azure what-if/apply、Entra/credential、Internet公開・スマートフォン実機は未実施 | 通知メール本人確認、Actions/GHCR権限、identity照合、実build/smokeとdigest、公開承認、匿名pull、Core / Protected各plan承認、apply後の認証・実機確認 |
| 未実装       | collector/publisherのOS service・startup登録はなく、明示commandでの起動が前提。unknown/attemptingの自動回収はなく、本人のactual確認と回収判断が前提                                                                              | 常駐化・自動回収は初回scope外。実装済みコードの未配備・未検証と混同しない                                                                          |

次の本人入力はBudget通知先メールとその確認日時である。公開対象head・digestと公開範囲の承認、およびwhat-if取得後のCore / Protected別plan/parameter承認は別の待機条件として残る。JPY確認だけで後続の公開操作を開始しない。
