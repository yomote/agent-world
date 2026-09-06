# Azure管理status IaC

`main.bicep`はsubscription scopeで管理専用Resource Groupを作り、`resources.bicep`へContainer Apps Consumption、非公開Blob、user-assigned managed identity、Key Vault、Log Analytics、Budgetを委譲する。Worldアプリ用Resource Groupを参照・変更しない。

初回`Core`はexternal ingressとEasy Authを無効にした状態で、全resourceがCreateだけのwhat-ifであることを確認する。Entra構成とKey Vault secretを用意した後の`Protected`はContainer AppのModifyと`authConfigs/current`のCreateだけを許可する。`Deploy-ManagementStatus.ps1`はDelete、Ignore、Unsupported、未許可Modifyで停止する。

`main.bicepparam.example`のplaceholderを実値にしたfileはcommitしない。imageは`@sha256:` digestだけを使う。`confirmedBillingCurrency=JPY`と通知メールはCost Managementと本人確認後だけ渡す。deploy scriptには実請求通貨の確認出典・日時と通知先の本人確認日時も渡し、head、全parameter、planと一緒にhashで承認対象へ固定する。what-ifもapplyもAzure CLIの本人contextと対象subscription / tenantを再照合する。

Container imageはrepository rootで次の配布内容だけをbuildする。

```powershell
docker build --file Dockerfile.status --tag agent-world-status:local .
docker run --rm -p 127.0.0.1:8020:8000 `
  -e AGENT_WORLD_STATUS_SNAPSHOT=/tmp/current.json `
  agent-world-status:local
```

`Dockerfile.status.dockerignore`は`apps/ops_status`、`docs/status`、`requirements-status.txt`以外をcontextから除外する。local mapping、session record、credential、`artifacts/`はimageへ入れない。

local Dockerを使えない環境では、mainへreview済み変更をmergeした後、`Management status image` workflowを承認済み40文字headで手動実行する。最初は`publish=false`でbuildとhealth smokeだけを確認し、HITL承認後の1回だけ`publish=true`にする。workflowはimage jobに限定した既存`GITHUB_TOKEN`の`packages:write`だけを使い、新しいPATやAzure resourceを要求しない。push結果が不明なら再dispatchせず、GHCR actualをread-onlyで一度確認する。push後のpackageは公開状態を確認し、匿名pullできなければAzure what-ifへ進まない。
