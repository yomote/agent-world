[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [ValidateSet('JPY')] [string] $ExpectedCurrency,
  [string] $AzureCli = 'az'
)

$ErrorActionPreference = 'Stop'
$request = @{
  type = 'ActualCost'
  timeframe = 'MonthToDate'
  dataset = @{
    granularity = 'None'
    aggregation = @{ totalCost = @{ name = 'PreTaxCost'; function = 'Sum' } }
  }
} | ConvertTo-Json -Depth 8 -Compress
$uri = "https://management.azure.com/subscriptions/$SubscriptionId/providers/Microsoft.CostManagement/query?api-version=2025-03-01"
$responseJson = & $AzureCli rest `
  --only-show-errors `
  --method post `
  --uri $uri `
  --headers Content-Type=application/json `
  --body $request `
  --output json
if ($LASTEXITCODE -ne 0) {
  throw 'Azure billing currency query failed; apply was not attempted and no retry was made.'
}

$response = $responseJson | ConvertFrom-Json
$columns = @($response.properties.columns.name)
$currencyIndex = [array]::IndexOf($columns, 'Currency')
if ($currencyIndex -lt 0 -or $response.properties.rows.Count -ne 1) {
  throw 'Azure billing currency response shape was unexpected; apply was not attempted.'
}
$currency = [string]$response.properties.rows[0][$currencyIndex]
if ($currency -ne $ExpectedCurrency) {
  throw "Azure billing currency is $currency; $ExpectedCurrency budget apply was not attempted."
}

Write-Output "Azure billing currency verified as $ExpectedCurrency."
