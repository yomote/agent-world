[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $ConfirmationPath,
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ConfirmationPath -PathType Leaf)) {
  throw 'Applyには初回の請求通貨確認記録が必要です。'
}

try {
  $confirmation = Get-Content -Raw -LiteralPath $ConfirmationPath | ConvertFrom-Json
} catch {
  throw '請求通貨確認記録をJSONとして読み取れません。'
}

$expectedScope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName"
$utcPattern = '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$'
function Test-UtcTimestamp($value) {
  if ($value -is [datetime]) { return $value.Kind -eq [DateTimeKind]::Utc }
  if ($value -is [datetimeoffset]) { return $value.Offset -eq [TimeSpan]::Zero }
  return ([string]$value) -match $utcPattern
}
if ($confirmation.schemaVersion -ne 1 `
    -or [string]::IsNullOrWhiteSpace([string]$confirmation.method) `
    -or [string]::IsNullOrWhiteSpace([string]$confirmation.budgetScopeConfirmationMethod) `
    -or ([string]$confirmation.currency) -cne 'JPY' `
    -or ([string]$confirmation.subscriptionId) -ine $SubscriptionId `
    -or ([string]$confirmation.budgetScopeResourceId) -ine $expectedScope `
    -or -not (Test-UtcTimestamp $confirmation.currencyConfirmedAtUtc) `
    -or -not (Test-UtcTimestamp $confirmation.budgetScopeConfirmedAtUtc)) {
  throw '請求通貨確認記録が対象subscription、Budget scope、JPY、確認方法、確認時刻と一致しません。'
}

Write-Output 'Initial JPY billing currency confirmation matches the deployment scope.'
