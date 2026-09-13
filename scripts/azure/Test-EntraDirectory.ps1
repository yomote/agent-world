[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [string] $TenantId,
  [Parameter(Mandatory)] [string] $EntraClientId,
  [Parameter(Mandatory)] [string[]] $AllowedPrincipalObjectIds
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$accountType = & az account show --only-show-errors --query user.type --output tsv
if ($accountType -ne 'user') { throw "Entra directory検査は本人のuser contextで実行してください。" }
$accountTenant = & az account show --only-show-errors --query tenantId --output tsv
if ($accountTenant -ne $TenantId) { throw "Selected tenant does not match the expected Entra tenant." }
$signedInUserId = & az ad signed-in-user show --only-show-errors --query id --output tsv
if ($LASTEXITCODE -ne 0 `
    -or @($AllowedPrincipalObjectIds).Count -ne 1 `
    -or $AllowedPrincipalObjectIds[0] -ne $signedInUserId) {
  throw "AllowedPrincipalObjectIdsは現在login中の本人1名と一致させてください。"
}

$app = (& az ad app show --only-show-errors --id $EntraClientId --output json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Entra app registration was not found." }
$fqdn = & az containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query 'properties.configuration.ingress.fqdn' --output tsv
$expectedCallback = "https://$fqdn/.auth/login/aad/callback"
if ($app.signInAudience -ne 'AzureADMyOrg' `
    -or -not $app.web.implicitGrantSettings.enableIdTokenIssuance `
    -or (Compare-Object @($expectedCallback) @($app.web.redirectUris))) {
  throw "Entra app registration single-tenant, ID token, or callback drift detected."
}
$sp = (& az ad sp show --only-show-errors --id $EntraClientId --output json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or -not $sp.appRoleAssignmentRequired) { throw "Entra service principal assignment requirement drift detected." }
foreach ($principalId in $AllowedPrincipalObjectIds) {
  $assignments = (& az rest --only-show-errors --method get --uri "https://graph.microsoft.com/v1.0/users/$principalId/appRoleAssignments" --output json | ConvertFrom-Json).value
  if ($LASTEXITCODE -ne 0 -or -not ($assignments | Where-Object resourceId -EQ $sp.id)) {
    throw "Allowed user is not assigned to the Entra enterprise app: $principalId"
  }
}
Write-Output "Entra directory registration and assignments: no drift"
