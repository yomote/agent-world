[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
  throw "Azure CLI が見つかりません。docs/runbooks/azure-bootstrap.md の公式インストール手順を実行してください。"
}

$accountJson = & az account show --only-show-errors --output json
if ($LASTEXITCODE -ne 0) {
  throw "Azure に未ログインです。az login --use-device-code を実行してください。"
}
$account = $accountJson | ConvertFrom-Json
if ($account.id -ne $SubscriptionId) {
  throw "選択中の subscription が指定値と一致しません。az account set --subscription <id> で明示してください。"
}
if ($account.state -ne "Enabled") {
  throw "指定した subscription は Enabled ではありません。"
}

Write-Output "Azure context verified: subscription=$SubscriptionId tenant=$($account.tenantId)"
