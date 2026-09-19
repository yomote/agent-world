# ADR 0006: Azure Container Appsへ単一コンテナで初回公開する

- 状態: 採用
- 日付: 2026-09-06

## 文脈

現行scopeは単一のAとmoveだけで、WorldStateは1プロセスのメモリ内にある。スマートフォンからHTTPSで使い、月1,000円を目安にコストと構成ドリフトを管理したい。永続化、複数worker、LLMは要求されていない。

## 判断

Reactのbuild成果をFastAPIが配信する単一imageをAzure Container Apps Consumptionへ置く。0.25 vCPU / 0.5 GiB、worker 1、min replicas 0、max replicas 1、active revision 1とする。再起動やscale-to-zero後はWorldが初期化される。

Azure Resource Managerの宣言とactualを直接比較でき、共有state backendを増やさないBicepを採用する。coreはResource Group、Container Apps environment/app、Log Analytics、Key Vault、managed identity、Budgetを管理し、`what-if`をdriftの正本にする。Entra app registrationはMicrosoft Graph資源なのでbootstrap scriptで作り、Bicepの`authConfigs`とread-only検査で期待値を固定する。

GitHub Actionsはenvironment subjectのOIDCで専用Resource GroupだけのContributorを使う。imageは公開GHCRのdigestで参照し、ACRの固定費とregistry credentialを持たない。本人限定時はsingle-tenant Entra registration、enterprise app assignment必須、Easy Authの本人object ID allowlistを重ねる。client secretはKey Vaultだけに保存し、GitHubへ渡さない。

初回ingress、what-if/apply、Graph伝播の回復、公開後のdrift照合、費用・請求通貨の確認は、[Azure初回bootstrap](../runbooks/azure-bootstrap.md)、[Azure deploy・検証・rollback](../runbooks/azure-deploy.md)、[Azure cost・構成ドリフト](../runbooks/azure-cost-drift.md)を正本とする。

## トレードオフ

scale-to-zeroは通常時のcompute費用を抑える一方、cold startとWorld resetがある。最大1 replicaはWorldの一貫性を守る一方、可用性と処理量を制限する。GHCR packageをpublicにするためimage自体は匿名取得できるが、sourceは既にpublicであり、アプリ入口はEntraで保護する。Key Vaultは資源と少額のoperation費を増やすが、secretを再deployやGitHubへ持ち回らずに済む。

Terraformは採用しない。GitHub設定のTerraformとはstateと責務を分け、AzureはBicep `what-if`を使う。
