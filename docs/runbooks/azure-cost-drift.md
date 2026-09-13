# Azure cost・構成ドリフト

`Azure cost and drift`は毎日03:17 JST頃と手動実行だけで起動する。並列実行せず、12分で終了する。変化なしではstep summaryだけを残し、drift、未評価の`Ignore`、認証差分、Cost Management失敗、50%閾値到達でrunを失敗させる。

public repositoryのscheduled workflowはrepository activityが60日ないとGitHubにより自動無効化され得る。月1回、Actions画面でscheduleがenabledであり、直近の日次runが24時間程度以内にあることを確認する。無効または欠落していれば原因と最後の成功runを確認し、手動runを1回成功させてから日次監視を再受入れする。Budget通知は別経路だがhard capではないため、schedule停止をBudgetだけで代替しない。

coreは `app.bicep` のResource Group `what-if`で比較する。desired imageには、現在mainからdeploy対象pathを最後に変更したcommitを履歴で特定し、その成功時に登録したSHA tagから解決したdigestを使う。Azureと無関係な後続mergeにimageがなくても誤検知せず、対象変更のdeploy失敗でimageがなければ検査を失敗させる。`Ignore`は差分なしではなく未検証として失敗する。認証はsecret値を読まず、次をactualから検査する。

- Easy Auth有効、HTTPS必須、`/healthz`だけ除外、他pathはEntraへredirect
- AAD provider、client ID、single-tenant issuer、本人object ID allowlist
- 他identity provider無効、token store無効
- Key Vault参照identityと、本人operatorのsecret rotation権限

Cost Managementは専用Resource GroupのMonth-to-date actualを取得し、請求通貨が設定値と違えば失敗する。50%でworkflowが失敗し、Budgetは50% actual、80% forecast、100% actualを確認済みメールへ通知する。

GitHub OIDCへtenant-wideなDirectory Reader権限は与えないため、Entra registrationの`AzureADMyOrg`、ID token、callback URI、enterprise appのassignment必須と本人assignmentは日次workflowで未実行と明記する。初回とcredential rotation後に、本人user contextで`Test-EntraDirectory.ps1`を実行する。

Budgetは支出を止めない。[Log Analyticsの日次上限](https://learn.microsoft.com/azure/azure-monitor/logs/daily-cap) 0.023 GB/日も到達時刻のずれと超過課金があり、到達後はログを観測できない。Container Apps max replica 1もhard capではなく、長時間trafficが続けばcomputeとnetwork費が増える。異常時はCost AnalysisとLog Analytics ingestionを確認し、不要trafficの遮断、ingress停止、ログdestination停止の順に影響を判断する。

429またはrate limitの403では`Retry-After`とrate-limit resetを守る。指定がなければ60秒、次は120秒待ち、read retryは初回後2回まで。同じrunを再起動して回数を戻さない。認証拒否や分類できない403は再試行しない。
