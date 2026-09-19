# Azure IaC

`main.bicep` は subscription scope で専用 Resource Group を作り、`app.bicep` へ Container Apps、Log Analytics、Key Vault、managed identity、任意の月次 Budget を委譲します。Easy Authのclient secretはKey Vaultだけに保存し、Container Appはmanaged identityでsecretを参照します。`auth.bicep` は Entra app registrationを作らず、既存 registration とsecret参照を使って本人の object IDだけを許可します。

日常の image 更新は `az containerapp update --image <digest>` だけを行います。core IaCは `app.bicep` の resource-group `what-if`、bootstrapだけで必要なKey Vault role assignmentはread-only actual検査、認証はsecretを取得せず `az containerapp auth show` の許可principalと拒否動作を検査します。Terraform stateを持たず、Azure Resource Manager自身のactual stateを比較元にできるため、初期構成はBicepを採用します。

Budgetの通貨はsubscriptionの請求通貨です。`1000` を渡す前にcost queryが `JPY` を返すことを確認します。開始日は月初UTCのRFC 3339日時です。通知先が空の間はBudget resourceを作りません。

## 現在の構成値

`app.bicep`のContainer Appは0.25 vCPU、0.5 GiB、min replicas 0、max replicas 1を宣言する。WorldStateが単一プロセスのメモリ内にあるため、max replicas 1は複数のWorldを並行に作らないための制約である。再起動とscale-to-zeroはWorldを初期化する。単一image、Bicep、identity、公開境界を選ぶ理由と代案は[ADR 0006](../../docs/adr/0006-azure-container-apps.md)に記録する。
