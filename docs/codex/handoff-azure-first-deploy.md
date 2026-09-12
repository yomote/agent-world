# Azure初回公開の引き渡し

## 現在地

PR #14のアプリ、Container Apps IaC、Entra本人限定認証、OIDC、cost / drift検査を引き継いだ。2026-09-06時点でAzure resource、Entra app registration、OIDC identity、GHCR packageは作成しておらず、外部公開もしていない。

2026-09-13の再開時点で、PR #14はcurrent main `1b245335cee6b9298dd165cb5166fddb85457783`から19 commits遅れ、GitHubで`CONFLICTING`だった。Azure公開対象をcurrent mainへ合わせるため同SHAをbranchへ統合した。競合は`apps/world/api.py`と`apps/world/tests/test_api.py`の2ファイルだけで、mainの共有Event historyとPR #14のhealth / production static配信を両方保持した。mainから入った管理status層は変更していない。

最初の固定後にmainへ入った非機能の文書・構成図2 commitsも競合なしで統合し、今回のpreflight基点を`c94baa8ce49b129aa988948fc27ef0d3b49007e6`に固定した。以後のmain更新はPR #14のmergeabilityまたはdeploy対象へ影響する場合だけ追加統合する。

必須check `container-check`が無関係なPRで生成されない問題は、`Deploy Azure` workflowをmain向けの全PRで起動し、production containerへの入力が変わった場合だけbuildする方式へ修正した。無関係なReady PRでも同じcheck名が軽量に成功する。Draftのskipは受入証跡にしない。

## 独立レビュー依頼packet

- base: `origin/main` (`c94baa8ce49b129aa988948fc27ef0d3b49007e6`、今回のpreflight固定基点)
- review対象: PR #14のcurrent head。過去review SHAは履歴であり、修正後SHAへ再reviewする
- 重点観点:
  - main向けの全Ready PRで`container-check`が生成されるか
  - forkを含むPRでbase / head SHAを安全かつ確実に比較できるか
  - container入力変更時だけbuild / smokeが実行され、差分判定失敗を成功扱いしないか
  - workflow権限、OIDC environment境界、main固定、同時deploy、未知の書き込み結果に対する停止が維持されるか
  - 初回公開packetの対象、費用、本人認証、rollback、未検証事項が実装と一致するか
- 必須証跡: 指摘のseverity・path・影響・最小修正、修正後SHAへの再レビュー、未検証のlive項目の明記

## 初回deploy承認packet

初回の有料resource作成、Entra権限作成、Internet公開を含む。まずcurrent headの独立レビューとローカル検証を揃え、PRのpush / Ready化とDocker CIへ進める具体的判断を提示する。mergeは自動化せず、current-head CI後に別の人間判断を残す。mainの実image、実請求通貨、Budget通知先を得た最終what-ifが揃ってから、Azure apply / Entra / OIDC / Internet公開の適用gateを提示する。

承認前にwhat-ifを含むread-only検査は実施できるが、`-Apply`、app registration作成、role assignment、GitHub environment書き込み、package公開は行わない。一度の包括承認で未確定の後続条件まで成立したとは扱わない。

### 対象と上限

| 項目               | 固定する値                                                                                                                                                 |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Azure subscription | 現在確認済みのEnabled subscription `omote-dev-subscription` 1件。実行時にIDとsigned-in userを再照合する                                                    |
| Region             | Japan East                                                                                                                                                 |
| Resource Group     | `rg-agent-world-jpe`（既存RGを流用しない）                                                                                                                 |
| Container App      | `agent-world-yomote-jpe`を候補とする。専用managed environment作成前はname availability APIを使えないため、初回what-ifとapply直前のRG不存在で衝突を検査する |
| Capacity           | 0.25 vCPU / 0.5 GiB、min replica 0、max replica 1、uvicorn worker 1                                                                                        |
| 公開範囲           | `/healthz`のみ匿名。UIと`/api/*`はEntra login必須                                                                                                          |
| 利用者             | 実行時にlogin中の本人Entra object ID 1件だけ                                                                                                               |
| Image              | Public `ghcr.io/yomote/agent-world`のcurrent main SHAをbuildし、解決済みdigestを固定                                                                       |
| GitHub OIDC        | `azure-production` environment、subjectは同environment、専用RGのContributorだけ。client secretなし                                                         |
| Budget             | 推奨packetでは実請求通貨と通知先を確認する。通貨がJPYなら月1,000円、50% actual / 80% forecast / 100% actual通知を初回applyに含める。現在は両方未確認       |

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

| 検査 / 操作                                                                    | 状態                                                                                        |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| ローカル実装、unit / build、独立レビュー                                       | 初回reviewの5指摘を修正し、修正stateのローカル再検証はPASS。固定SHAの限定reviewは未実行     |
| GitHub `azure-production`のmain限定 / admin bypass無効                         | APIでactual確認済み                                                                         |
| Azure subscription / provider / regionのread-only確認                          | 2026-09-13に再確認。Enabled 1件、本人user、Japan East、必要provider登録済み                 |
| 専用Resource Group                                                             | 2026-09-13に`rg-agent-world-jpe`が存在しないことを確認                                      |
| Cost Management通貨 / 当月cost                                                 | 2026-09-13 01:44 JSTの新preflightで1回だけ照会し429。再試行0で停止、未確認                  |
| GHCR build / push / package公開 / 匿名pull                                     | 未実行                                                                                      |
| Azure subscription / RG scopeのwhat-if                                         | placeholder digest・Budgetなしで実行しCreate 8件のみ。実image/Budget付き最終what-ifは未実行 |
| Azure / Entra / OIDC / role assignment / GitHub environment variableの書き込み | **未実行**                                                                                  |
| Internet公開 / smartphone確認                                                  | **未実行**                                                                                  |

### 承認前に残る本人判断

本人から必要な未回答は、Budget通知を受け取るメールアドレスである。

Issue #8は月1,000円を設計上の目安としており、ADR 0006とBicepの既定値も1000で一致する。この金額は既決の目安であってhard capではない。推奨案は、実請求通貨と通知先を確認し、通貨がJPYなら月1,000円のBudgetを同じapplyに含められるまでresource作成を保留する。JPYは確認済みの実請求通貨ではない。Cost Managementが再び429になる、実通貨がJPYでない、または通知先が得られない場合は初回apply前に停止し、金額を勝手に読み替えたりBudgetなしで公開したりしない。

2026-09-06の照会は429後に既定の60秒・120秒待機とread retry上限を使い切ったが、応答の`Retry-After` / reset値は証跡に残っていない。2026-09-13 01:44 JSTは別taskから1週間経過し明示resumeされた新preflightとしてsubscription scopeを1回だけ照会した。再び429だったため、今回のshared budgetはrequests 1、retries 0で停止した。Azure CLIの安全な出力には今回も`Retry-After` / reset値がなく、次回再開時刻は確定していない。

承認前のread-only preflightでは、subscription名とstate、本人user context、本人object IDを取得できること、Japan East、必要provider、専用RG不存在を秘密値を出さず再照合した。未公開imageの代わりにゼロのplaceholder digestを使ったsubscription what-ifは、専用RG、Container App / environment、Key Vault、managed identity、Log Analytics、Key Vault role 2件のCreate 8件だけで、Modify / Deleteは0件だった。Budget通知先が空なのでBudgetはこの差分に含まれない。この結果は実deployment image、実通貨、通知先を入れたapply直前の最終what-ifの代わりにはしない。

### 準備と適用gate

1. 固定したPR headをpushし、Ready化してcurrent-head `check`と`container-check`を実行する。両方の成功と独立reviewを提示し、PR #14をmergeするか人間が判断する。自動mergeは行わない。
2. merge承認後、`Deploy Azure`を`image_only=true`、確認文字列`publish-ghcr-image`で手動実行し、current mainのsource imageを一度buildしてGHCRへSHA tagでpushする。通常のmain pushはbootstrap enablementと全Azure変数が揃う前にimageを書き込めない。package公開の承認を確認してpublic化し、匿名pullで解決したdigestを記録する。
3. subscription alias `omote-dev-subscription`、tenant、本人object ID、Japan East、専用RG `rg-agent-world-jpe`の不存在、Container App候補`agent-world-yomote-jpe`をread-onlyで再照合する。Cost Managementの再照会は新しいretry budgetと再開条件を明示できる場合だけ1回行う。
4. 実image digest、実通貨、確認済み通知先で`Deploy-AzureCore.ps1`を`-Apply`なしで実行する。専用RG内のCreateだけで削除・既存更新がなく、Budget Createを含む結果とFullResourcePayloadsの内容を含むwhat-if SHA256を提示してAzure適用の明示承認を得る。ここまでの準備承認をapply承認へ流用しない。
5. 適用承認後、同じ引数へ確認済みJPYと承認済みwhat-if SHA256、`-Apply`を加える。scriptはCost ManagementでJPYを再確認し、通知先非空、専用RG不存在、Create-only allowlist、plan hash一致を満たす場合だけinternal ingressのcore、Key Vault、managed identity、Log Analytics、Budgetを作る。自己申告のJPY文字列だけをactual billing確認の代わりにしない。
6. `Configure-Entra.ps1`でsingle-tenant app、本人assignment、1年secretを作る。secret値はKey Vaultだけへ保存する。
7. Entra DirectoryとEasy Authのactualが本人1件、HTTPS必須、匿名path `/healthz`だけであることを確認する。
8. Entra引数と`-ExternalIngress`を加えたwhat-ifを確認後、applyする。直後に未認証UI / APIの拒否とhealthを検査する。
9. 専用RG scopeのOIDC identityを作成し、保護済み`azure-production` environmentへ非secret変数を設定する。
10. `Deploy Azure`を手動で1回実行し、OIDC login、digest deploy、HTTPS / auth smokeを確認する。
11. 本人がスマートフォンでlogin、move、Event / position一致を確認する。別アカウント拒否と再起動resetは確認できる場合だけ証跡にする。

各書き込みの結果が不明なら自動再送しない。actualを読んでから復旧方針を決める。

### rollback

- application回帰: 直前に成功したdigestへContainer Appを1回だけ更新し、auth / health / UI smokeを再実行する。
- 認証境界の不一致: external ingressを無効化し、本人限定actualが一致するまで公開完了としない。
- external適用直後のFQDN取得またはauth smoke失敗: scriptが`az containerapp ingress disable`を1回だけ実行し、actualがexternalでないことを読む。書き込み失敗・結果不明でも再送せずactual確認後に停止する。
- OIDC権限の不一致: GitHub environmentを使用停止にし、専用RG外のrole assignmentを回収する。
- 初回core失敗: 自動再applyせずdeployment operationとactual resourceを確認する。Resource Group削除は別の破壊操作として対象を確認する。

## 検証

- 独立レビュー: `d4e882d9a718abad1a3c6f4fa724cfbd9de6265d`は履歴。`8332a33a8f4514fef5b88bfdba3fb47ec4aa0ad6`でHigh 3件・Medium 2件を検出し、5件すべてを修正。修正後current head reviewは未実行
- `npm run check`: 修正stateでPASS（Vitest 24、Node 5、pytest 418、build）
- Azure guard / workflow追加回帰test 23件: PASS
- workflow / runbookのPrettier、PythonのRuff、actionlint 1.7.12: PASS
- `npm run iac:check`: PASS（CI固定Terraform 1.16.1、fmt / init / validate / mock test 1）
- `npm run docs:check`: PASS（CI固定lychee 0.24.2、160 total / 96 unique links、error 0）
- Bicep 0.46.1の4 template buildは補助証跡としてPASS。修正stateのAzure PowerShell script parse: PASS
- production単一origin smoke: health、UI、World、Event history、move、未知API 404がPASS
- Docker daemon: ローカルにないため未実行。Dockerのproduction build / runはcurrent-head CIで確認する
- GitHub Actions上の新しい`container-check`: 未検証
- Azure / Entra / GHCR / OIDCのlive操作: 未実施
