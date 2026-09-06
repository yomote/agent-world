[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $AllowedUserObjectId
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$accountType = & az account show --only-show-errors --query user.type --output tsv
$signedInUserId = & az ad signed-in-user show --only-show-errors --query id --output tsv
if ($accountType -ne 'user' -or $signedInUserId -ne $AllowedUserObjectId) {
  throw "AllowedUserObjectIdは現在login中の本人object IDと一致させてください。"
}
$fqdn = & az containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query 'properties.configuration.ingress.fqdn' --output tsv
if ($LASTEXITCODE -ne 0 -or -not $fqdn) { throw "Container App が見つかりません。core deploymentを先に実行してください。" }
$displayName = "$AppName-login"
$existing = & az ad app list --display-name $displayName --query '[].appId' --output tsv --only-show-errors
if ($LASTEXITCODE -ne 0) { throw "Entra app registration lookup failed." }
if ($existing) { throw "同名のapp registrationが既にあります。重複作成せずactualを確認してください: $displayName" }

$redirectUri = "https://$fqdn/.auth/login/aad/callback"
$clientId = & az ad app create --only-show-errors --display-name $displayName --sign-in-audience AzureADMyOrg --enable-id-token-issuance true --web-redirect-uris $redirectUri --query appId --output tsv
if ($LASTEXITCODE -ne 0 -or -not $clientId) { throw "Entra app registration creation failed." }
$servicePrincipalId = & az ad sp create --only-show-errors --id $clientId --query id --output tsv
if ($LASTEXITCODE -ne 0 -or -not $servicePrincipalId) { throw "Entra service principal creation failed." }
& az ad sp update --only-show-errors --id $servicePrincipalId --set appRoleAssignmentRequired=true --output none
if ($LASTEXITCODE -ne 0) { throw "Entra assignment requirement update failed." }

$credentialDisplayName = "agent-world-easy-auth-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$credentialKeyId = $null
$credentialExpiresOn = $null
$clientSecret = $null
try {
  $credentialJson = & az ad app credential reset --only-show-errors --id $clientId --display-name $credentialDisplayName --years 1 --output json
  if ($LASTEXITCODE -ne 0 -or -not $credentialJson) { throw "Entra client secret creation failed." }
  $clientSecret = ($credentialJson | ConvertFrom-Json).password
  if (-not $clientSecret) {
    throw "Entra credential response had no password. actualを確認してください: clientId=$clientId displayName=$credentialDisplayName"
  }
  $credentialJson = $null
  Remove-Variable credentialJson -ErrorAction SilentlyContinue

  # `credential reset` returns the secret value, while keyId/expiry are obtained
  # separately so recovery never depends on printing or persisting that value.
  $credentialMetadataJson = & az ad app credential list --only-show-errors --id $clientId --output json
  if ($LASTEXITCODE -ne 0 -or -not $credentialMetadataJson) {
    throw "Credential metadata lookup failed. actualを確認してください: clientId=$clientId displayName=$credentialDisplayName"
  }
  $matchingCredentials = @($credentialMetadataJson | ConvertFrom-Json | Where-Object displayName -eq $credentialDisplayName)
  if ($matchingCredentials.Count -ne 1) {
    throw "Credential metadataを一意に特定できません。actualを確認してください: clientId=$clientId displayName=$credentialDisplayName"
  }
  $credentialKeyId = $matchingCredentials[0].keyId
  $credentialExpiresOn = $matchingCredentials[0].endDateTime
  if (-not $credentialKeyId -or -not $credentialExpiresOn) {
    throw "Credential metadata was incomplete. actualを確認してください: clientId=$clientId displayName=$credentialDisplayName"
  }
} catch {
  $clientSecret = $null
  Remove-Variable clientSecret -ErrorAction SilentlyContinue
  throw
} finally {
  $credentialJson = $null
  $credentialMetadataJson = $null
  $matchingCredentials = $null
  Remove-Variable credentialJson -ErrorAction SilentlyContinue
  Remove-Variable credentialMetadataJson -ErrorAction SilentlyContinue
  Remove-Variable matchingCredentials -ErrorAction SilentlyContinue
}
$keyVaultName = & az keyvault list --only-show-errors --resource-group $ResourceGroupName --query "[?tags.application=='agent-world'].name | [0]" --output tsv
$identityId = & az containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query 'keys(identity.userAssignedIdentities)[0]' --output tsv
if (-not $keyVaultName -or -not $identityId) { throw "Bicep managed Key Vault or app identity was not found." }
$vaultUri = "https://$keyVaultName.vault.azure.net"
$accessToken = & az account get-access-token --only-show-errors --resource https://vault.azure.net --query accessToken --output tsv
if ($LASTEXITCODE -ne 0 -or -not $accessToken) { throw "Key Vault access token acquisition failed." }
try {
  $secretBody = @{
    value = $clientSecret
    attributes = @{ enabled = $true }
    tags = @{ entraCredentialKeyId = $credentialKeyId; entraCredentialDisplayName = $credentialDisplayName; expiresOn = $credentialExpiresOn }
  } | ConvertTo-Json -Compress
  try {
    $null = Invoke-RestMethod -Method Put -Uri "$vaultUri/secrets/easy-auth-client-secret`?api-version=7.4" -Headers @{ Authorization = "Bearer $accessToken" } -ContentType 'application/json' -Body $secretBody
  } catch {
    throw "Key Vault格納失敗。残ったEntra credential keyId=$credentialKeyId displayName=$credentialDisplayName expiresOn=$credentialExpiresOn をactual確認し、必要なら az ad app credential delete --id $clientId --key-id $credentialKeyId で回収してください。"
  }
} finally {
  $secretBody = $null
  $clientSecret = $null
  $accessToken = $null
  Remove-Variable secretBody -ErrorAction SilentlyContinue
  Remove-Variable clientSecret -ErrorAction SilentlyContinue
  Remove-Variable accessToken -ErrorAction SilentlyContinue
}
$secretReference = "microsoft-provider-authentication-secret=keyvaultref:$vaultUri/secrets/easy-auth-client-secret,identityref:$identityId"
$null = & az containerapp secret set --only-show-errors --resource-group $ResourceGroupName --name $AppName --secrets $secretReference --output none
if ($LASTEXITCODE -ne 0) { throw "Container App Key Vault secret reference update failed." }

$assignment = @{ principalId = $AllowedUserObjectId; resourceId = $servicePrincipalId; appRoleId = '00000000-0000-0000-0000-000000000000' } | ConvertTo-Json -Compress
$null = & az rest --only-show-errors --method post --uri "https://graph.microsoft.com/v1.0/users/$AllowedUserObjectId/appRoleAssignments" --body $assignment --output none
if ($LASTEXITCODE -ne 0) { throw "本人のEntra app assignment failed. Auth config was not deployed." }

$tenantId = & az account show --query tenantId --output tsv
$authParameterFile = [System.IO.Path]::GetTempFileName()
try {
  @{
    '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
    contentVersion = '1.0.0.0'
    parameters = @{
      appName = @{ value = $AppName }
      tenantId = @{ value = $tenantId }
      clientId = @{ value = $clientId }
      allowedPrincipalObjectIds = @{ value = @($AllowedUserObjectId) }
    }
  } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $authParameterFile -Encoding utf8NoBOM
  $null = & az deployment group create --only-show-errors --resource-group $ResourceGroupName --template-file "$PSScriptRoot/../../infra/azure/auth.bicep" --parameters "@$authParameterFile" --name agent-world-auth --output none
  if ($LASTEXITCODE -ne 0) { throw "Container Apps Entra auth deployment failed." }
} finally {
  Remove-Item -LiteralPath $authParameterFile -Force -ErrorAction SilentlyContinue
}
Write-Output "Entra configured: clientId=$clientId allowedUserObjectId=$AllowedUserObjectId redirectUri=$redirectUri"
Write-Output "Credential metadata: keyId=$credentialKeyId displayName=$credentialDisplayName expiresOn=$credentialExpiresOn"
Write-Output "Client secret value was stored only in Key Vault and was not printed. Rotate before expiry."
