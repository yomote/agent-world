# Azure初回bootstrap

## 前提と既定

Microsoft公式WinGet packageからBicep 0.46.1、Azure CLI 2.90.0を導入済み。新しいshellで次を確認する。

```powershell
bicep --version
az version
az account show
```

未ログインの場合に必要な人の操作は `az login --use-device-code` 1回だけ。複数のEnabled subscriptionがある場合だけ利用先を選び、`az account set --subscription <id>`で固定する。現在観測したEnabled subscriptionは1件で、regionはJapan Eastを既定にする。別アプリのResource Groupは流用しない。

既定は月1,000円目安、本人限定。初回に対象subscriptionの請求通貨とResource GroupのBudget scopeをJPYへ対応付け、確認方法・対象・UTC時刻をprivate JSONへ保存する。本人が確認できるBudget通知先も得るまではBudgetを作らない。現時点の見積は、低頻度本人利用なら[Container Appsの月間無料枠](https://azure.microsoft.com/pricing/details/container-apps/)内、Log Analyticsは[課金アカウントごとの最初の5 GB/月無料枠](https://azure.microsoft.com/pricing/details/monitor/)に対して最大約0.713 GB/月の設定、Key Vaultは少数operationなので、既存無料枠が未消費ならほぼ0円と予想する暫定値である。契約、既存利用、通信、攻撃的traffic、為替で変わり、1,000円以内を保証しない。

private JSONは次のschemaを使う。請求通貨の確認とBudget scopeへの適用確認は証拠と時点が異なるため、2つのmethodとUTC timestampを分ける。値の推測、別subscriptionや別scopeの記録、不正または未来の日時はApply guardが拒否する。実際のIDはprivateファイルだけへ保存する。

```json
{
  "schemaVersion": 1,
  "method": "Azure BillingProperty REST 2024-04-01",
  "subscriptionId": "<subscription-id>",
  "budgetScopeResourceId": "/subscriptions/<subscription-id>/resourceGroups/<resource-group>",
  "currency": "JPY",
  "currencyConfirmedAtUtc": "<UTC timestamp ending in Z>",
  "budgetScopeConfirmationMethod": "Azure Portal Budgets list: same-subscription resource-group budgets displayed in JPY; target resource group not created",
  "budgetScopeConfirmedAtUtc": "<UTC timestamp ending in Z>"
}
```

## imageを用意する

mainのcommit SHAだけをtagに使い、push後のdigestを記録する。

```powershell
$sha = git rev-parse origin/main
$image = "ghcr.io/yomote/agent-world:$sha"
docker build --pull --tag $image .
docker push $image
docker buildx imagetools inspect $image
```

GitHub Packagesで最初のcontainer packageをpublicにする。public sourceから作るimageだけを含み、この変更は元に戻せないため対象package名を確認する。`Test-GhcrPublic.ps1`が匿名manifest取得に成功するまでAzureへdeployしない。

SHA tagはbuildし直せば上書き可能で、それ自体は不変保証ではない。Container App、deploy証跡、rollbackでは解決後の`sha256:` digestだけを使う。

Dockerを使える承認済みlocal環境がない場合は、PR #14のcurrent-head CIと人間によるmerge判断を終えた後、`Deploy Azure`を`image_only=true`、確認文字列`publish-ghcr-image`で手動実行する。このmodeはcurrent mainのimageをGHCRへpushするがAzure login / deployは行わない。通常のmain pushとrepository dispatchは、`AZURE_DEPLOY_ENABLED=true`とAzure / Entra / Budget変数が揃う前にGHCRへ書き込めずfail-closedになる。

## coreのwhat-ifと適用

月初値は `2026-09-01T00:00:00Z` のようなRFC 3339日時を使う。最初は`-Apply`なしで差分と対象Resource Groupを確認する。

```powershell
./scripts/azure/Deploy-AzureCore.ps1 `
  -SubscriptionId <subscription-id> `
  -ResourceGroupName rg-agent-world-jpe `
  -Location japaneast `
  -AppName <globally-unique-app-name> `
  -Image ghcr.io/yomote/agent-world@sha256:<digest> `
  -BudgetStartDate 2026-09-01T00:00:00Z `
  -BudgetAmount 1000 `
  -BudgetContactEmails <confirmed-email> `
  -BillingCurrencyConfirmationPath <private-confirmation-json> `
  -OperatorPrincipalObjectId <signed-in-user-object-id>
```

what-ifで新規専用Resource Groupと意図した資源だけがCreateになり、削除・既存更新がないこと、Budgetを含むことを確認する。scriptがFullResourcePayloadsの内容から出す`WHAT-IF SHA256`を適用承認packetへ記録する。承認後、同じ引数へ`-ApprovedWhatIfSha256 <approved-hash> -Apply`を加える。Applyはprivate記録が対象subscription、Budget scope、JPY、初回確認方法、UTC時刻と一致することをローカル検査し、通知先非空、専用RG不存在、Create-only allowlist、plan hash一致を満たさなければ停止する。通常applyは通貨確認のためにAzure APIやCost Management Queryを呼ばない。subscription、Budget scope、請求契約を変更するときは記録を更新する。初回coreはingressをinternalに固定するため、UI/APIはInternetから到達できない。

## 本人限定Entraを構成する

signed-in userのobject IDは `az ad signed-in-user show --query id -o tsv` から導出する。次のscriptはsingle-tenant app registration、ID token、正確なcallback、enterprise app assignment必須、本人assignmentを設定する。1年client secretは値を出力せずKey Vaultへ直接保存し、Container Appはmanaged identityで参照する。

```powershell
./scripts/azure/Configure-Entra.ps1 `
  -SubscriptionId <subscription-id> `
  -ResourceGroupName rg-agent-world-jpe `
  -AppName <app-name> `
  -AllowedUserObjectId <signed-in-user-object-id>
```

Entra authのactual設定後、同じcore引数に`-TenantId <tenant-id> -EntraClientId <client-id> -EnableEntraAuth -ExternalIngress`を追加してwhat-ifを確認する。新しいhashの承認後に`-ApprovedWhatIfSha256 <approved-hash> -Apply`を加える。scriptは外部公開前にDirectoryのsingle-tenant registration・本人assignmentと、Easy Authの本人1名allowlist、未認証redirect、HTTPS必須、匿名pathが`/healthz`だけ、他provider無効であるactualを検査する。外部公開deploymentがnonzeroやtimeout相当の応答で終わった場合は再送せず、createに使った同じdeployment名のprovisioning stateを1回読む。Succeeded / Failed / Canceledのterminal stateを確認できた場合だけingress actualを読み、externalなら1回だけ無効化し、internalなら書き込まない。nonterminal、空、読取失敗は`EXTERNAL DEPLOYMENT UNKNOWN`として停止し、後で公開へ変化し得るため、同deploymentとingressのmanual actual確認まで完了を主張しない。公開変更の直後にもHTTPSのhealthと未認証UI/API拒否をsmokeする。FQDN取得またはsmokeが失敗した場合はexternal ingressを1回だけ無効化し、actualを読み、再送せず公開失敗として停止する。

途中失敗時は自動再送しない。scriptが出す`clientId`、一意なcredential `displayName`、取得済みなら`keyId`と期限を使い、app registration、enterprise app、Key Vault secret、authConfigのactualを確認してから復旧する。secret値は再取得できない。Key Vault格納前に止まったcredentialはmetadataを確認し、`az ad app credential delete --id <client-id> --key-id <key-id>`で回収する。

app registrationとservice principalの作成直後、`appRoleAssignmentRequired`更新だけがGraphの`Resource does not exist`で失敗した場合はConfigure全体を再実行しない。app/SPが各1件、同じtenant/client ID/SP object ID、single-tenant、正確なcallback、ID token有効であり、credential・本人assignment・Easy Authがまだ存在しないことをread-only actualで確認する。その既知stateに限り、同じコマンドへ`-ResumeTenantId`、`-ResumeClientId`、`-ResumeServicePrincipalObjectId`を追加して一度だけ再開する。対象違いまたは想定外の副作用があれば停止する。

Configureの引数は、resume引数なしのnew create、上記3 IDsだけのpartial resume、3 IDsに`-ResumeOrphanCredentialKeyId`と`-ResumeOrphanCredentialDisplayName`を加えるcredential recoveryの3形だけを許可する。後者はmetadata特定失敗で今回作成された未使用credentialがexact 1件、SPのassignment必須が有効、本人assignment・Key Vault secret・Container App secret参照・Easy Authが未作成、ingressがinternalである場合に限る。exact credentialを1回削除し、actual 0件を確認してから新credentialを1件発行する。削除結果またはactualが不明なら再送せず、新credentialを発行しない。

期限前のrotationでは、一意なdisplay nameで新しいcredentialを`--append`作成し、metadataのkey IDと期限を控え、同じKey Vault secret名を更新する。versionless Key Vault参照の更新反映には最大30分かかり得るため、旧credentialを最低35分は併存させる。その後、新credentialがKey Vaultのlatest versionであるactualと本人loginを確認してから、古いcredentialをkey ID指定で削除する。初回構成時とrotation後は本人user contextでDirectory側も確認する。

```powershell
./scripts/azure/Test-EntraDirectory.ps1 `
  -SubscriptionId <subscription-id> `
  -ResourceGroupName rg-agent-world-jpe `
  -AppName <app-name> `
  -TenantId <tenant-id> `
  -EntraClientId <client-id> `
  -AllowedPrincipalObjectIds <signed-in-user-object-id>
```

## GitHub OIDC

core作成後に `Setup-GitHubOidc.ps1` を実行する。federated subjectは `repo:yomote/agent-world:environment:azure-production`、権限scopeは専用Resource GroupのContributorだけ。client secretは作らない。出力したIDと、runbookのアプリ・budget・auth値をGitHub environment variablesへ設定する。

OIDCは設定が存在するだけでは合格にしない。`Deploy Azure`を手動実行し、GitHub runのOIDC login、対象RGだけのimage更新、HTTPS smokeが成功することを確認する。別RGへの書き込み拒否は破壊的でないread-only scope確認で代替し、試験資源を作らない。

OIDC作成前に工場側`infra/github`を適用し、read-onlyの`Test-GitHubEnvironment.ps1`を実行する。`azure-production` environmentのresponseにBooleanのadmin bypass無効が明示され、custom deployment branch policyが`main` 1件だけであるactualを確認する。field欠落は無効と推測せず失敗する。workflowにもmain判定を置くが、任意branchの改変workflowへenvironment OIDCを渡さない本当の境界はこのGitHub側policyである。

`Set-GitHubEnvironmentVariables.ps1 -EnableDeployment`でOIDC ID、専用RG、app、auth、本人OID、実測したbudget通貨・通知先をenvironment variablesへまとめて設定する。scriptはEntra auth、JPY、通知先非空を要求し、同時に`AZURE_DEPLOY_ENABLED=true`を設定する。いずれもcredentialではなく、Easy Auth client secretは含めない。同scriptは設定後にenvironment branch policyも再検証する。
