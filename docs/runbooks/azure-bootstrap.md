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

Entra authのactual設定後、full core templateのwhat-ifは証拠としてprivate保存する。既存resourceのprovider既定値・reference式・配列順序が`Modify`として現れる場合、それらを広いnoise allowlistで無視してcore全体を再適用しない。公開変更は次の専用scriptでEntra app callbackとContainer App ingressの2対象だけに限定する。

```powershell
./scripts/azure/Publish-AzureExternalIngress.ps1 `
  -SubscriptionId <subscription-id> `
  -ResourceGroupName rg-agent-world-jpe `
  -Location japaneast `
  -AppName <app-name> `
  -TenantId <tenant-id> `
  -EntraClientId <client-id> `
  -AllowedUserObjectId <signed-in-user-object-id> `
  -Image ghcr.io/yomote/agent-world@sha256:<digest> `
  -EvidenceDirectory <private-ignored-directory>
```

`EvidenceDirectory`はrepositoryのignored `artifacts/`配下だけを指定する。scriptはDirectoryのsingle-tenant registration・本人assignmentと、Easy Authの本人1名allowlist、未認証redirect、HTTPS必須、匿名pathが`/healthz`だけ、他provider無効であるactualを検査する。さらにSucceeded状態、UserAssigned identity、単一workerの0.25 vCPU / 0.5 GiB、3 probe、min 0 / max 1、対象RGの`application=agent-world` Key Vault exact 1件とsecret URLを公開前baselineとして固定する。internal FQDNが`<APP_NAME>.internal.<ENVIRONMENT_UNIQUE_ID>.<REGION>.azurecontainerapps.io`のexact形式である場合だけ、同じsuffixからpublic FQDNを組み立てる。callbackをinternalからpublicへexact 1件更新してactual確認した後、公式CLIでingressをexternal、port 8000、transport auto、allow-insecure falseへ1回だけ変更する。公開後はFQDN、image digest、UAMI、Key Vault secret reference、Container Appの残構成、auth全体、callback以外のEntra app構成、本人assignmentが保存beforeと一致することを確認し、HTTPS healthと未認証UI/API拒否をsmokeする。write結果、FQDN、immutable構成、auth、smokeのいずれかが不明または不一致なら、Container Appのprovisioning stateがterminalでexternalと確定した場合だけingressをinternalへ1回戻し、terminalかつactual falseと元のinternal FQDNを確認してからcallbackを元のinternal URLへ戻す。nonterminal/read不能は後からexternalへ変わり得るため`EXTERNAL PUBLICATION UNKNOWN`で停止し、競合するcontainmentやcallback writeを送らない。ingressがterminalかつ元のinternal actualのままならingress writeを追加せずcallbackだけ戻す。各結果が不明なら再送せず停止し、`ingress disable`をinternal復旧の代用にしない。

専用公開が成功しても、full core templateのdriftが0件になったとは扱わない。同じdigest・Budget・auth・external入力の`FullResourcePayloads` what-ifを公開後にread-onlyで1回保存し、公開前に保存したprovider既定値・reference式・配列順序の差分と照合する。既知差分が恒常的に残る場合はそのまま未解消driftとして記録し、広いallowlistや未確認PASSで隠さない。before/after snapshot、smoke、post-what-ifを既存の構成図JSON/generator照合へ渡すまでactual図の完了としない。

公開に使用したprivate parameter fileをそのまま使い、post-what-ifを削除される一時fileではなく保存する。

```powershell
$postPlan = 'artifacts/azure-preflight/external-public-post-what-if-full.private.json'
az deployment sub what-if `
  --location japaneast `
  --template-file infra/azure/main.bicep `
  --parameters '@artifacts/azure-preflight/external-auth-parameters.private.json' `
  --result-format FullResourcePayloads `
  --no-pretty-print `
  --output json |
  Set-Content -LiteralPath $postPlan -Encoding utf8NoBOM
Get-FileHash -Algorithm SHA256 -LiteralPath $postPlan
```

exit code、status、changeType別件数、各resource IDとbefore/afterを、公開前の`external-auth-what-if-full.private.json`と比較する。既知の5 Modifyを無条件にPASSへ変換せず、公開後にも残る差分として記録する。`external-before.private.json`、`external-after.private.json`、smoke結果、post-plan path/hash/drift内訳を`artifacts/azure-preflight/diagram-actual-handoff.private.json`へまとめ、Issue #38 / PR #53が指定する既存JSON/generatorの照合入力へ渡す。raw plan保存済みとactual図更新済みは別statusとして記録する。

途中失敗時は自動再送しない。scriptが出す`clientId`、一意なcredential `displayName`、取得済みなら`keyId`と期限を使い、app registration、enterprise app、Key Vault secret、authConfigのactualを確認してから復旧する。secret値は再取得できない。Key Vault格納前に止まったcredentialはmetadataを確認し、`az ad app credential delete --id <client-id> --key-id <key-id>`で回収する。

app registrationとservice principalの作成後に途中失敗した場合はConfigure全体を再実行しない。app/SPが各1件、同じtenant/client ID/SP object ID、single-tenant、正確なcallback、ID token有効であり、`appRoleAssignmentRequired`が明示的なboolean、credential 0件、本人assignment・Easy Authがまだ存在しないことをread-only actualで確認する。その既知stateに限り、同じコマンドへ`-ResumeTenantId`、`-ResumeClientId`、`-ResumeServicePrincipalObjectId`を追加して一度だけ再開する。`appRoleAssignmentRequired`がfalseなら更新し、trueなら重複更新を省いて次のstageへ進む。対象違いまたは想定外の副作用があれば停止する。

Configureの引数は、resume引数なしのnew create、上記3 IDsだけのpartial resume、3 IDsに`-ResumeOrphanCredentialKeyId`と`-ResumeOrphanCredentialDisplayName`を加えるcredential recoveryの3形だけを許可する。後者はmetadata特定失敗で今回作成された未使用credentialがexact 1件、SPのassignment必須が有効、本人assignment・Key Vault secret・Container App secret参照・Easy Authが未作成、ingressがinternalである場合に限る。exact credentialを1回削除し、actual 0件を確認してから新credentialを1件発行する。削除結果またはactualが不明なら再送せず、新credentialを発行しない。

credential発行とKey Vault格納の後、Container App secret参照の設定前に停止した場合はcredentialを削除・再発行せず、`Resume-EntraAfterKeyVault.ps1`で残工程だけを再開する。private記録のtenant/client/SP/user、credential key/display/expiry、Key Vault secret versionを入力し、app/SP/callback、assignment必須、credential exact 1件、本人assignment 0件、Easy Auth無効、internal ingress、対象RGで`application=agent-world`のKey Vault exact 1件、同じcredentialを示す有効なsecret version exact 1件、Container App secret参照 0件がすべて一致する場合に限る。scriptはKey Vaultのversion metadataと`az containerapp secret list`の既定metadataだけを使い、`--show-values`やsecret値を取得しない。承認済みUAMIのARM ID exact 1件をJSONから選び、既存Key Vault secretへのversionless参照設定、本人assignment、auth deploymentを各1回進める。actualが不明または各writeが失敗した場合は後続を止め、同じstageを自動再送しない。

Container App secret参照の設定後、本人assignment前に停止した場合は、同じ入力へ`-ResumeExistingSecretReference`を追加する。上記の共通条件に加え、secret metadataがexact 1件で、名前、versionless Key Vault URL、UAMI ARM IDが承認済み値と一致する場合だけ参照設定を省き、本人assignmentとauth deploymentへ進む。assignment bodyはIDだけを含む一時JSONへ保存し、`Content-Type=application/json`と`@file`で渡して終了時に削除する。既存参照の再設定、credential再発行、secret値の取得は行わない。

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
