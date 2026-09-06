# Azure初回公開の引き渡し

## 現在地

PR #14のアプリ、Container Apps IaC、Entra本人限定認証、OIDC、cost / drift検査を引き継いだ。2026-09-06時点でAzure resource、Entra app registration、OIDC identity、GHCR packageは作成しておらず、外部公開もしていない。

必須check `container-check`が無関係なPRで生成されない問題は、`Deploy Azure` workflowをmain向けの全PRで起動し、production containerへの入力が変わった場合だけbuildする方式へ修正した。無関係なReady PRでも同じcheck名が軽量に成功する。Draftのskipは受入証跡にしない。

## 独立レビュー依頼packet

- base: `origin/main` (`37f95ab6b2710779477d91371fcdb44edb2799d4`)
- review対象: PR #14のcurrent headに、この引き渡しのworkflow修正を加えたcommit
- 重点観点:
  - main向けの全Ready PRで`container-check`が生成されるか
  - forkを含むPRでbase / head SHAを安全かつ確実に比較できるか
  - container入力変更時だけbuild / smokeが実行され、差分判定失敗を成功扱いしないか
  - workflow権限、OIDC environment境界、main固定、同時deploy、未知の書き込み結果に対する停止が維持されるか
  - 初回公開packetの対象、費用、本人認証、rollback、未検証事項が実装と一致するか
- 必須証跡: 指摘のseverity・path・影響・最小修正、修正後SHAへの再レビュー、未検証のlive項目の明記

## 初回deploy承認packet

初回の有料resource作成、Entra権限作成、Internet公開を含むため、以下の全項目をcurrent headの独立レビュー後に一括で提示し、ユーザーの明示承認を得てから実行する。承認前にwhat-ifを含むread-only検査は実施できるが、`-Apply`、app registration作成、role assignment、GitHub environment書き込み、package公開は行わない。

### 対象と上限

| 項目               | 固定する値                                                                                                                                           |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Azure subscription | 現在確認済みのEnabled subscription `omote-dev-subscription` 1件。実行時にIDとsigned-in userを再照合する                                              |
| Region             | Japan East                                                                                                                                           |
| Resource Group     | `rg-agent-world-jpe`（既存RGを流用しない）                                                                                                           |
| Container App      | `agent-world-yomote-jpe`を候補とし、what-if前に名前の利用可否を確認する                                                                              |
| Capacity           | 0.25 vCPU / 0.5 GiB、min replica 0、max replica 1、uvicorn worker 1                                                                                  |
| 公開範囲           | `/healthz`のみ匿名。UIと`/api/*`はEntra login必須                                                                                                    |
| 利用者             | 実行時にlogin中の本人Entra object ID 1件だけ                                                                                                         |
| Image              | Public `ghcr.io/yomote/agent-world`のcurrent main SHAをbuildし、解決済みdigestを固定                                                                 |
| GitHub OIDC        | `azure-production` environment、subjectは同environment、専用RGのContributorだけ。client secretなし                                                   |
| Budget             | 推奨packetでは実請求通貨と通知先を確認する。通貨がJPYなら月1,000円、50% actual / 80% forecast / 100% actual通知を初回applyに含める。現在は両方未確認 |

作成対象は次のとおり。Resource Group外の既存resourceを変更しない。

| Resource / 権限                    | 構成                                                                                             |
| ---------------------------------- | ------------------------------------------------------------------------------------------------ |
| Container Apps managed environment | Consumption、zone redundancyなし                                                                 |
| Container App                      | Consumption、single active revision、0.25 vCPU / 0.5 GiB、min 0 / max 1、HTTP同時要求scale閾値10 |
| Log Analytics workspace            | `PerGB2018`、30日保持、日次上限0.023 GB（最大約0.713 GB / 31日）                                 |
| Key Vault                          | Standard、RBAC、purge protection有効、soft delete 7日、public network有効                        |
| User-assigned managed identity     | Container AppからKey Vault secretを読むための専用identity                                        |
| Key Vault role                     | app identityへ`Key Vault Secrets User`、本人へ`Key Vault Secrets Officer`                        |
| Entra                              | single-tenant app registrationとservice principal、assignment必須、本人1名だけをassignment       |
| Easy Auth credential               | 有効期限1年のclient secret 1件。値は表示・GitHub保存せずKey Vaultだけへ保存                      |
| GitHub deploy identity             | client secretなしのfederated credential、専用RGだけの`Contributor`                               |
| GitHub environment                 | 既存`azure-production`。mainだけを許可し、admin bypass無効。Azure ID等は非secret variables       |

匿名公開されるのはGHCR imageと`/healthz`だけである。Container AppのUIと`/api/*`はHTTPSとEntra本人認証を必須にする。Key Vaultのpublic networkは有効だが、data planeはRBACで上記2主体だけに限定する。

既存の暫定見積は、低頻度の本人利用、Container AppsとLog Analyticsの契約上の無料枠が他resourceに消費されていないことを前提に「ほぼ0円」である。請求通貨と当月実績を取得できておらず、価格計算による月額は未検証である。契約、既存利用、通信、traffic、為替で変わる。

Budgetは通知でありhard capではない。Budget未作成は金額通知もないことを意味する。max replica 1、min 0、Log Analytics日次上限で急増を抑えるが、1,000円以内は保証しない。初回公開後は実測costを記録し、通貨と通知先が確定するまでBudgetを「設定済み」と報告しない。

### 実行済みと未実行

| 検査 / 操作                                                                    | 状態                                                             |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| ローカル実装、unit / build、独立レビュー                                       | 実行済み。結果は本書末尾                                         |
| GitHub `azure-production`のmain限定 / admin bypass無効                         | APIでactual確認済み                                              |
| Azure subscription / provider / regionのread-only確認                          | 既存担当が実行済み。Enabled subscription 1件、Japan East利用可   |
| Cost Management通貨 / 当月cost                                                 | 429後、規定の60秒・120秒再試行を使い切り未確認。今回再試行しない |
| GHCR build / push / package公開 / 匿名pull                                     | 未実行                                                           |
| Azure subscription / RG scopeのwhat-if                                         | **未実行**                                                       |
| Azure / Entra / OIDC / role assignment / GitHub environment variableの書き込み | **未実行**                                                       |
| Internet公開 / smartphone確認                                                  | **未実行**                                                       |

### 承認前に残る本人判断

本人から必要な未回答は、Budget通知を受け取るメールアドレスである。

推奨案は、実請求通貨と通知先を確認し、Budgetを同じapplyに含められるまでresource作成を保留する。JPYは提案するBudget額の通貨であり、確認済みの実請求通貨ではない。Cost Managementが再び429になる、実通貨がJPYでない、または通知先が得られない場合は初回apply前に停止し、金額を勝手に読み替えたりBudgetなしで公開したりしない。Container App候補名の利用可否、subscription ID、tenant ID、本人object IDは秘密値を会話へ出さず、承認後のread-only preflightでactualを照合する。

### 承認後の実行順

1. current mainのsource imageを一度buildし、GHCRへSHA tagでpushする。packageをpublicにした後、匿名pullを確認してdigestを記録する。
2. subscription、tenant、本人object ID、region、Container App名をread-onlyで再照合する。Cost Managementはrate-limit解除後に1回だけ照会する。
3. `Deploy-AzureCore.ps1`を`-Apply`なしで実行し、専用RG内のCreateだけで削除・既存更新がないことを確認する。Budgetなしを許容していない場合は、JPY・通知先・Budget Createもここで確認する。
4. 同じ引数で`-Apply`し、internal ingressのcore、Key Vault、managed identity、Log Analyticsを作る。
5. `Configure-Entra.ps1`でsingle-tenant app、本人assignment、1年secretを作る。secret値はKey Vaultだけへ保存する。
6. Entra DirectoryとEasy Authのactualが本人1件、HTTPS必須、匿名path `/healthz`だけであることを確認する。
7. Entra引数と`-ExternalIngress`を加えたwhat-ifを確認後、applyする。直後に未認証UI / APIの拒否とhealthを検査する。
8. 専用RG scopeのOIDC identityを作成し、保護済み`azure-production` environmentへ非secret変数を設定する。
9. `Deploy Azure`を手動で1回実行し、OIDC login、digest deploy、HTTPS / auth smokeを確認する。
10. 本人がスマートフォンでlogin、move、Event / position一致を確認する。別アカウント拒否と再起動resetは確認できる場合だけ証跡にする。

各書き込みの結果が不明なら自動再送しない。actualを読んでから復旧方針を決める。

### rollback

- application回帰: 直前に成功したdigestへContainer Appを1回だけ更新し、auth / health / UI smokeを再実行する。
- 認証境界の不一致: external ingressを無効化し、本人限定actualが一致するまで公開完了としない。
- OIDC権限の不一致: GitHub environmentを使用停止にし、専用RG外のrole assignmentを回収する。
- 初回core失敗: 自動再applyせずdeployment operationとactual resourceを確認する。Resource Group削除は別の破壊操作として対象を確認する。

## 検証

- 独立レビュー: `d4e882d9a718abad1a3c6f4fa724cfbd9de6265d`で初回High 1件・Medium 1件の解消を確認し、追加指摘なし
- `npm run check`: PASS（Vitest 6、pytest 43、buildを含む）
- `npm run test:factory`: PASS（15）
- 追加回帰test: PASS
- workflow / runbookのPrettier、PythonのRuff: PASS
- `npm run iac:check`: このworktreeのPATHにTerraformがなく未実行
- `npm run docs:check`: このworktreeのPATHにlycheeがなく未実行
- GitHub Actions上の新しい`container-check`: 未検証
- Azure / Entra / GHCR / OIDCのlive操作: 未実施
