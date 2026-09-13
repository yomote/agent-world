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

& "$PSScriptRoot/Complete-EntraAssignmentAndAuth.ps1" `
  -SubscriptionId $SubscriptionId `
  -ResourceGroupName $ResourceGroupName `
  -AppName $AppName `
  -TenantId $TenantId `
  -ClientId $ClientId `
  -ServicePrincipalObjectId $ServicePrincipalObjectId `
  -AllowedUserObjectId $AllowedUserObjectId
