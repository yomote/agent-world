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

| 項目               | 固定する値                                                                                                        |
| ------------------ | ----------------------------------------------------------------------------------------------------------------- |
| Azure subscription | 現在確認済みのEnabled subscription `omote-dev-subscription` 1件。実行時にIDとsigned-in userを再照合する           |
| Region             | Japan East                                                                                                        |
| Resource Group     | `rg-agent-world-jpe`（既存RGを流用しない）                                                                        |
| Container App      | `agent-world-yomote-jpe`を候補とし、what-if前に名前の利用可否を確認する                                           |
| Capacity           | 0.25 vCPU / 0.5 GiB、min replica 0、max replica 1、uvicorn worker 1                                               |
| 公開範囲           | `/healthz`のみ匿名。UIと`/api/*`はEntra login必須                                                                 |
| 利用者             | 実行時にlogin中の本人Entra object ID 1件だけ                                                                      |
| Image              | Public `ghcr.io/yomote/agent-world`のcurrent main SHAをbuildし、解決済みdigestを固定                              |
| GitHub OIDC        | `azure-production` environment、subjectは同environment、専用RGのContributorだけ。client secretなし                |
| Budget             | 請求通貨JPYと通知先が未確認なので初回は作成しない。確認後も月1,000円、50% actual / 80% forecast / 100% actual通知 |

Budget未作成は支出上限がないことを意味する。max replica 1、min 0、Log Analytics日次上限で急増を抑えるが、1,000円以内は保証しない。初回公開後は実測costを記録し、通貨と通知先が確定するまでBudgetを「設定済み」と報告しない。

### 承認後の実行順

1. current mainのsource imageを一度buildし、GHCRへSHA tagでpushする。packageをpublicにした後、匿名pullを確認してdigestを記録する。
2. `Deploy-AzureCore.ps1`を`-Apply`なしで実行し、専用RG内のCreateだけで削除・既存更新がないことを確認する。
3. 同じ引数で`-Apply`し、internal ingressのcore、Key Vault、managed identity、Log Analyticsを作る。
4. `Configure-Entra.ps1`でsingle-tenant app、本人assignment、1年secretを作る。secret値はKey Vaultだけへ保存する。
5. Entra DirectoryとEasy Authのactualが本人1件、HTTPS必須、匿名path `/healthz`だけであることを確認する。
6. Entra引数と`-ExternalIngress`を加えたwhat-ifを確認後、applyする。直後に未認証UI / APIの拒否とhealthを検査する。
7. 専用RG scopeのOIDC identityを作成し、保護済み`azure-production` environmentへ非secret変数を設定する。
8. `Deploy Azure`を手動で1回実行し、OIDC login、digest deploy、HTTPS / auth smokeを確認する。
9. 本人がスマートフォンでlogin、move、Event / position一致を確認する。別アカウント拒否と再起動resetは確認できる場合だけ証跡にする。

各書き込みの結果が不明なら自動再送しない。actualを読んでから復旧方針を決める。

### rollback

- application回帰: 直前に成功したdigestへContainer Appを1回だけ更新し、auth / health / UI smokeを再実行する。
- 認証境界の不一致: external ingressを無効化し、本人限定actualが一致するまで公開完了としない。
- OIDC権限の不一致: GitHub environmentを使用停止にし、専用RG外のrole assignmentを回収する。
- 初回core失敗: 自動再applyせずdeployment operationとactual resourceを確認する。Resource Group削除は別の破壊操作として対象を確認する。

## 検証

- `npm run check`: PASS（Vitest 6、pytest 42、buildを含む）
- `npm run test:factory`: PASS（15）
- 追加回帰test: PASS
- workflow / runbookのPrettier、PythonのRuff: PASS
- `npm run iac:check`: このworktreeのPATHにTerraformがなく未実行
- `npm run docs:check`: このworktreeのPATHにlycheeがなく未実行
- GitHub Actions上の新しい`container-check`: 未検証
- Azure / Entra / GHCR / OIDCのlive操作: 未実施
