[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [ValidateRange(1, 1000000)] [decimal] $BudgetAmount,
  [string] $ExpectedCurrency = "JPY",
  [ValidateRange(1, 1000)] [int] $AlertPercent = 50
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$scope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName"
$uri = "https://management.azure.com$scope/providers/Microsoft.CostManagement/query?api-version=2025-03-01"
$request = @{
  type = "ActualCost"
  timeframe = "MonthToDate"
  dataset = @{
    granularity = "None"
    aggregation = @{ totalCost = @{ name = "PreTaxCost"; function = "Sum" } }
  }
} | ConvertTo-Json -Depth 8 -Compress
$responseJson = & az rest --only-show-errors --method post --uri $uri --headers Content-Type=application/json --body $request --output json
if ($LASTEXITCODE -ne 0) { throw "Azure Cost Management query failed." }
$response = $responseJson | ConvertFrom-Json
$columns = @($response.properties.columns.name)
$costIndex = [array]::IndexOf($columns, "PreTaxCost")
$currencyIndex = [array]::IndexOf($columns, "Currency")
if ($costIndex -lt 0 -or $currencyIndex -lt 0 -or $response.properties.rows.Count -ne 1) {
  throw "Azure cost response shape was unexpected."
}
$cost = [decimal]$response.properties.rows[0][$costIndex]
$currency = [string]$response.properties.rows[0][$currencyIndex]
if ($currency -ne $ExpectedCurrency) {
  throw "請求通貨は $currency です。$ExpectedCurrency 前提のBudgetを適用しません。"
}
$percent = if ($BudgetAmount -eq 0) { 0 } else { [math]::Round(($cost / $BudgetAmount) * 100, 2) }
Write-Output ("Month-to-date cost: {0:N2} {1} / budget {2:N0} {1} ({3}%)" -f $cost, $currency, $BudgetAmount, $percent)
if ($percent -ge $AlertPercent) {
  throw "Month-to-date cost reached the $AlertPercent% alert threshold. Budget alerts and this check are not a hard cap."
}
