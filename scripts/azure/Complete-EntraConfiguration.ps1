[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [string] $KeyVaultName,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $TenantId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ClientId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ServicePrincipalObjectId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $AllowedUserObjectId
)

$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId

# Avoid JMESPath functions here. On Windows az.cmd expands `%*` inside a batch
# block, where parentheses in `keys(...)[0]` are parsed by cmd.exe.
$identitiesJson = & az containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query identity.userAssignedIdentities --output json
if ($LASTEXITCODE -ne 0 -or -not $identitiesJson) { throw 'Container App user-assigned identity actualを読み取れません。' }
$identities = $identitiesJson | ConvertFrom-Json
$identityProperties = @($identities.PSObject.Properties)
$expectedIdentityId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.ManagedIdentity/userAssignedIdentities/$AppName-identity"
if ($identityProperties.Count -ne 1 -or $identityProperties[0].Name -ine $expectedIdentityId) {
  throw 'Container App user-assigned identityが承認済みのexact 1件と一致しません。'
}
$identityId = $identityProperties[0].Name

$vaultsJson = & az keyvault list --only-show-errors --resource-group $ResourceGroupName --output json
if ($LASTEXITCODE -ne 0 -or -not $vaultsJson) { throw 'Bicep managed Key Vault actualを読み取れません。' }
try {
  $vaultDocument = [System.Text.Json.JsonDocument]::Parse($vaultsJson)
  if ($vaultDocument.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Array) {
    throw 'Bicep managed Key Vault JSON root must be an array.'
  }
} finally {
  if ($vaultDocument) { $vaultDocument.Dispose() }
}
$vaults = @($vaultsJson | ConvertFrom-Json | Where-Object { $_.tags.application -ceq 'agent-world' })
$expectedVaultId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.KeyVault/vaults/$KeyVaultName"
if ($vaults.Count -ne 1 -or $vaults[0].name -cne $KeyVaultName -or $vaults[0].id -ine $expectedVaultId) {
  throw 'Bicep managed Key Vaultが承認済みのexact 1件と一致しません。'
}
$vaultUri = "https://$KeyVaultName.vault.azure.net"
$secretReference = "microsoft-provider-authentication-secret=keyvaultref:$vaultUri/secrets/easy-auth-client-secret,identityref:$identityId"
$null = & az containerapp secret set --only-show-errors --resource-group $ResourceGroupName --name $AppName --secrets $secretReference --output none
if ($LASTEXITCODE -ne 0) { throw 'Container App Key Vault secret reference update failed.' }

$assignment = @{ principalId = $AllowedUserObjectId; resourceId = $ServicePrincipalObjectId; appRoleId = '00000000-0000-0000-0000-000000000000' } | ConvertTo-Json -Compress
$null = & az rest --only-show-errors --method post --uri "https://graph.microsoft.com/v1.0/users/$AllowedUserObjectId/appRoleAssignments" --body $assignment --output none
if ($LASTEXITCODE -ne 0) { throw '本人のEntra app assignment failed. Auth config was not deployed.' }

$authParameterFile = [System.IO.Path]::GetTempFileName()
try {
  @{
    '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
    contentVersion = '1.0.0.0'
    parameters = @{
      appName = @{ value = $AppName }
      tenantId = @{ value = $TenantId }
      clientId = @{ value = $ClientId }
      allowedPrincipalObjectIds = @{ value = @($AllowedUserObjectId) }
    }
  } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $authParameterFile -Encoding utf8NoBOM
  $null = & az deployment group create --only-show-errors --resource-group $ResourceGroupName --template-file "$PSScriptRoot/../../infra/azure/auth.bicep" --parameters "@$authParameterFile" --name agent-world-auth --output none
  if ($LASTEXITCODE -ne 0) { throw 'Container Apps Entra auth deployment failed.' }
} finally {
  Remove-Item -LiteralPath $authParameterFile -Force -ErrorAction SilentlyContinue
}
