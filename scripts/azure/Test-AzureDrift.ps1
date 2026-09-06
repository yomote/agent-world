[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $Location,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [ValidatePattern('^ghcr\.io/.+@sha256:[0-9a-f]{64}$')] [string] $Image,
  [Parameter(Mandatory)] [string] $BudgetStartDate,
  [ValidateRange(1, 1000000)] [int] $BudgetAmount = 1000,
  [string[]] $BudgetContactEmails = @(),
  [Parameter(Mandatory)] [ValidateSet('entra', 'public')] [string] $AuthMode,
  [string[]] $AllowedPrincipalObjectIds = @(),
  [string] $EntraClientId = '',
  [string] $TenantId = '',
  [Parameter(Mandatory)] [string] $OperatorPrincipalObjectId
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$parameters = @{
  location = @{ value = $Location }
  appName = @{ value = $AppName }
  image = @{ value = $Image }
  budgetAmount = @{ value = $BudgetAmount }
  budgetStartDate = @{ value = $BudgetStartDate }
  budgetContactEmails = @{ value = $BudgetContactEmails }
  enableEntraAuth = @{ value = ($AuthMode -eq 'entra') }
  externalIngress = @{ value = $true }
}
$parameterFile = [System.IO.Path]::GetTempFileName()
try {
  @{ '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'; contentVersion = '1.0.0.0'; parameters = $parameters } |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $parameterFile -Encoding utf8NoBOM
  $whatIfJson = & az deployment group what-if --only-show-errors --resource-group $ResourceGroupName --template-file "$PSScriptRoot/../../infra/azure/app.bicep" --parameters "@$parameterFile" --result-format ResourceIdOnly --no-pretty-print --output json
  if ($LASTEXITCODE -ne 0) { throw "Azure what-if failed." }
} finally {
  Remove-Item -LiteralPath $parameterFile -Force -ErrorAction SilentlyContinue
}
$whatIfChanges = @(($whatIfJson | ConvertFrom-Json).changes)
$ignored = @($whatIfChanges | Where-Object changeType -EQ 'Ignore')
if ($ignored.Count -gt 0) {
  $ignored | Select-Object resourceId, changeType | Format-Table | Out-String | Write-Output
  throw "Azure what-if returned Ignore; configuration is unverified."
}
$changes = @($whatIfChanges | Where-Object changeType -NE 'NoChange')
if ($changes.Count -gt 0) {
  $changes | Select-Object resourceId, changeType | Format-Table | Out-String | Write-Output
  throw "Azure core configuration drift detected."
}

$identityPrincipalId = & az identity show --only-show-errors --resource-group $ResourceGroupName --name "$AppName-identity" --query principalId --output tsv
$keyVaultId = & az keyvault list --only-show-errors --resource-group $ResourceGroupName --query "[?tags.application=='agent-world'].id | [0]" --output tsv
if ($LASTEXITCODE -ne 0 -or -not $identityPrincipalId -or -not $keyVaultId) { throw "Managed identity or Key Vault actual state is missing." }
$secretsUserRoleId = "/subscriptions/$SubscriptionId/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"
$roleCount = & az role assignment list --only-show-errors --scope $keyVaultId --query "[?principalId=='$identityPrincipalId' && roleDefinitionId=='$secretsUserRoleId'] | length(@)" --output tsv
if ($LASTEXITCODE -ne 0 -or [int]$roleCount -ne 1) { throw "Key Vault Secrets User role assignment drift detected." }
$secretsOfficerRoleId = "/subscriptions/$SubscriptionId/providers/Microsoft.Authorization/roleDefinitions/b86a8fe4-44ce-4948-aee5-eccb2c155cd7"
$operatorRoleCount = & az role assignment list --only-show-errors --scope $keyVaultId --query "[?principalId=='$OperatorPrincipalObjectId' && roleDefinitionId=='$secretsOfficerRoleId'] | length(@)" --output tsv
if ($LASTEXITCODE -ne 0 -or [int]$operatorRoleCount -ne 1) { throw "Operator Key Vault Secrets Officer role assignment drift detected." }

$authJson = & az containerapp auth show --only-show-errors --resource-group $ResourceGroupName --name $AppName --output json 2>$null
if ($AuthMode -eq "public") {
  if ($LASTEXITCODE -eq 0 -and ($authJson | ConvertFrom-Json).platform.enabled) {
    throw "Expected public mode, but Container Apps authentication is enabled."
  }
} else {
  if (-not $EntraClientId -or -not $TenantId) { throw "EntraClientId and TenantId are required for Entra drift checks." }
  if ($LASTEXITCODE -ne 0) { throw "Expected Entra auth config was not found." }
  $auth = $authJson | ConvertFrom-Json
  $actualPrincipals = @($auth.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.identities)
  $expectedIssuer = "https://login.microsoftonline.com/$TenantId/v2.0"
  if (-not $auth.platform.enabled `
      -or $auth.globalValidation.unauthenticatedClientAction -ne 'RedirectToLoginPage' `
      -or $auth.globalValidation.redirectToProvider -ne 'azureactivedirectory' `
      -or (Compare-Object @('/healthz') @($auth.globalValidation.excludedPaths)) `
      -or -not $auth.httpSettings.requireHttps `
      -or $auth.login.tokenStore.enabled `
      -or -not $auth.identityProviders.azureActiveDirectory.enabled `
      -or $auth.identityProviders.azureActiveDirectory.registration.clientId -ne $EntraClientId `
      -or $auth.identityProviders.azureActiveDirectory.registration.openIdIssuer -ne $expectedIssuer `
      -or $auth.identityProviders.azureActiveDirectory.registration.clientSecretSettingName -ne 'microsoft-provider-authentication-secret') {
    throw "Entra authentication security settings drift detected."
  }
  if ($AllowedPrincipalObjectIds.Count -eq 0 -or (Compare-Object $AllowedPrincipalObjectIds $actualPrincipals)) {
    throw "Entra allowed principal list drift detected."
  }

  function Test-EnabledProvider($value) {
    if ($null -eq $value) { return $false }
    if ($value -is [System.Array]) { return [bool]($value | Where-Object { Test-EnabledProvider $_ } | Select-Object -First 1) }
    if ($value -is [pscustomobject]) {
      if ($value.PSObject.Properties.Name -contains 'enabled' -and $value.enabled) { return $true }
      foreach ($property in $value.PSObject.Properties) {
        if (Test-EnabledProvider $property.Value) { return $true }
      }
    }
    return $false
  }
  foreach ($provider in $auth.identityProviders.PSObject.Properties) {
    if ($provider.Name -ne 'azureActiveDirectory' -and (Test-EnabledProvider $provider.Value)) {
      throw "Unexpected identity provider is enabled: $($provider.Name)"
    }
  }

}

Write-Output "Azure core, RBAC, and Container Apps auth configuration: no drift"
Write-Output "Entra directory objects: not_run (requires user-context Test-EntraDirectory.ps1)"
