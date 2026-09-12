[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $AzureClientId,
  [Parameter(Mandatory)] [string] $AzureTenantId,
  [Parameter(Mandatory)] [string] $AzureSubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $Location,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [ValidateSet('entra', 'public')] [string] $AuthMode,
  [Parameter(Mandatory)] [string] $OperatorPrincipalObjectId,
  [string] $EntraClientId = '',
  [int] $BudgetAmount = 1000,
  [Parameter(Mandatory)] [string] $BudgetCurrency,
  [Parameter(Mandatory)] [string] $BudgetStartDate,
  [string[]] $BudgetContactEmails = @(),
  [Parameter(Mandatory)] [switch] $EnableDeployment,
  [string] $Repository = "yomote/agent-world",
  [string] $Environment = "azure-production"
)

$ErrorActionPreference = "Stop"
if (-not $EnableDeployment) { throw "Deployment enablement requires an explicit switch." }
if ($AuthMode -ne 'entra' -or $BudgetCurrency -ne 'JPY' -or $BudgetContactEmails.Count -eq 0) {
  throw "Deployment requires Entra auth, verified JPY billing currency, and at least one Budget contact."
}
& "$PSScriptRoot/Test-GitHubEnvironment.ps1" -Repository $Repository -Environment $Environment
$credentialLines = "protocol=https`nhost=github.com`n`n" | git credential fill
$credential = @{}
foreach ($line in $credentialLines) {
  if ($line -match '^([^=]+)=(.*)$') { $credential[$matches[1]] = $matches[2] }
}
if (-not $credential.password) { throw "GitHub credential was not available from GCM." }
$headers = @{
  Accept = 'application/vnd.github+json'
  Authorization = "Bearer $($credential.password)"
  'X-GitHub-Api-Version' = '2022-11-28'
  'User-Agent' = 'agent-world-azure-bootstrap'
}
try {
  $repo = Invoke-RestMethod -Method Get -Uri "https://api.github.com/repos/$Repository" -Headers $headers
  $base = "https://api.github.com/repositories/$($repo.id)/environments/$Environment/variables"
  $existing = Invoke-RestMethod -Method Get -Uri "$base`?per_page=100" -Headers $headers
  if ($existing.total_count -gt 100) { throw "Environment variables exceed bounded query." }
  $values = [ordered]@{
    AZURE_CLIENT_ID = $AzureClientId
    AZURE_TENANT_ID = $AzureTenantId
    AZURE_SUBSCRIPTION_ID = $AzureSubscriptionId
    AZURE_RESOURCE_GROUP = $ResourceGroupName
    AZURE_LOCATION = $Location
    AZURE_CONTAINER_APP = $AppName
    AZURE_AUTH_MODE = $AuthMode
    AZURE_OPERATOR_PRINCIPAL_ID = $OperatorPrincipalObjectId
    AZURE_ENTRA_CLIENT_ID = $EntraClientId
    AZURE_ALLOWED_PRINCIPAL_IDS_JSON = ConvertTo-Json -InputObject @($OperatorPrincipalObjectId) -Compress
    AZURE_BUDGET_AMOUNT = [string]$BudgetAmount
    AZURE_BUDGET_CURRENCY = $BudgetCurrency
    AZURE_BUDGET_START_DATE = $BudgetStartDate
    AZURE_BUDGET_CONTACT_EMAILS_JSON = ConvertTo-Json -InputObject @($BudgetContactEmails) -Compress
    AZURE_DEPLOY_ENABLED = 'true'
  }
  foreach ($entry in $values.GetEnumerator()) {
    $body = @{ name = $entry.Key; value = $entry.Value } | ConvertTo-Json -Compress
    if (@($existing.variables.name) -contains $entry.Key) {
      $null = Invoke-RestMethod -Method Patch -Uri "$base/$($entry.Key)" -Headers $headers -ContentType 'application/json' -Body $body
    } else {
      $null = Invoke-RestMethod -Method Post -Uri $base -Headers $headers -ContentType 'application/json' -Body $body
    }
  }
  $actual = Invoke-RestMethod -Method Get -Uri "$base`?per_page=100" -Headers $headers
  foreach ($entry in $values.GetEnumerator()) {
    $actualEntry = @($actual.variables | Where-Object name -EQ $entry.Key)
    if ($actualEntry.Count -ne 1 -or $actualEntry[0].value -ne $entry.Value) {
      throw "GitHub environment variable actual mismatch: $($entry.Key)"
    }
  }
} finally {
  $credential.Clear()
  Remove-Variable credentialLines -ErrorAction SilentlyContinue
}
Write-Output "GitHub environment policy and non-secret Azure variables were configured and read back from API."
