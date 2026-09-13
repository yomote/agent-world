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
$billingPropertyMethod = 'Azure BillingProperty REST 2024-04-01'
$budgetScopeMethod = 'Azure Portal Budgets list: same-subscription resource-group budgets displayed in JPY; target resource group not created'
function Test-PastUtcTimestamp($value) {
  if ($value -is [datetime]) {
    if ($value.Kind -ne [DateTimeKind]::Utc) { return $false }
    $parsed = [DateTimeOffset]$value
  } elseif ($value -is [datetimeoffset]) {
    if ($value.Offset -ne [TimeSpan]::Zero) { return $false }
    $parsed = $value
  } else {
    $text = [string]$value
    if ($text -notmatch 'Z$') { return $false }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        $text,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsed
      )) { return $false }
    if ($parsed.Offset -ne [TimeSpan]::Zero) { return $false }
  }

  return $parsed -le [DateTimeOffset]::UtcNow.AddMinutes(5)
}
if ($confirmation.schemaVersion -isnot [long] `
    -or $confirmation.schemaVersion -ne 1 `
    -or ([string]$confirmation.method) -cne $billingPropertyMethod `
    -or ([string]$confirmation.budgetScopeConfirmationMethod) -cne $budgetScopeMethod `
    -or ([string]$confirmation.currency) -cne 'JPY' `
    -or ([string]$confirmation.subscriptionId) -ine $SubscriptionId `
    -or ([string]$confirmation.budgetScopeResourceId) -ine $expectedScope `
    -or -not (Test-PastUtcTimestamp $confirmation.currencyConfirmedAtUtc) `
    -or -not (Test-PastUtcTimestamp $confirmation.budgetScopeConfirmedAtUtc)) {
  throw '請求通貨確認記録が対象subscription、Budget scope、JPY、確認方法、確認時刻と一致しません。'
}

Write-Output 'Initial JPY billing currency confirmation matches the deployment scope.'
