[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $AllowedUserObjectId,
  [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ResumeTenantId = '',
  [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ResumeClientId = '',
  [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ResumeServicePrincipalObjectId = '',
  [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ResumeOrphanCredentialKeyId = '',
  [string] $ResumeOrphanCredentialDisplayName = ''
)

$ErrorActionPreference = "Stop"
$resumeValues = @($ResumeTenantId, $ResumeClientId, $ResumeServicePrincipalObjectId) | Where-Object { $_ }
$orphanValues = @($ResumeOrphanCredentialKeyId, $ResumeOrphanCredentialDisplayName) | Where-Object { $_ }
$validResumeShape = ($resumeValues.Count -eq 0 -and $orphanValues.Count -eq 0) `
  -or ($resumeValues.Count -eq 3 -and $orphanValues.Count -eq 0) `
  -or ($resumeValues.Count -eq 3 -and $orphanValues.Count -eq 2)
if (-not $validResumeShape) {
  throw 'Entra引数はnew create、3 IDsのpartial resume、3 IDsとorphan key/displayのcredential recoveryだけを許可します。'
}
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$accountType = & az account show --only-show-errors --query user.type --output tsv
$signedInUserId = & az ad signed-in-user show --only-show-errors --query id --output tsv
if ($accountType -ne 'user' -or $signedInUserId -ne $AllowedUserObjectId) {
  throw "AllowedUserObjectIdは現在login中の本人object IDと一致させてください。"
}
$ingressJson = & az containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query 'properties.configuration.ingress' --output json
if ($LASTEXITCODE -ne 0 -or -not $ingressJson) { throw "Container App が見つかりません。core deploymentを先に実行してください。" }
$ingress = $ingressJson | ConvertFrom-Json
$fqdn = $ingress.fqdn
if (-not $fqdn) { throw "Container App ingress FQDNを確認できません。" }
$displayName = "$AppName-login"
$servicePrincipalAssignmentRequired = $false
$existing = @(& az ad app list --display-name $displayName --query '[].appId' --output tsv --only-show-errors)
if ($LASTEXITCODE -ne 0) { throw "Entra app registration lookup failed." }

$redirectUri = "https://$fqdn/.auth/login/aad/callback"
if ($resumeValues.Count -eq 3) {
  if ($null -eq $ingress.external -or [bool]$ingress.external) {
    throw 'Resume前のContainer App ingressはinternalのactualが必須です。'
  }
  if ($existing.Count -ne 1 -or $existing[0] -ine $ResumeClientId) {
    throw 'Resume対象以外のapp registrationまたは重複appがあります。'
  }
  $actualTenantId = & az account show --only-show-errors --query tenantId --output tsv
  if ($LASTEXITCODE -ne 0 -or -not $actualTenantId) { throw 'Resume tenant actualを読み取れません。' }
  $applicationJson = & az ad app show --only-show-errors --id $ResumeClientId --output json
  if ($LASTEXITCODE -ne 0 -or -not $applicationJson) { throw 'Resume app actualを読み取れません。' }
  $servicePrincipalJson = & az ad sp show --only-show-errors --id $ResumeClientId --output json
  if ($LASTEXITCODE -ne 0 -or -not $servicePrincipalJson) { throw 'Resume service principal actualを読み取れません。' }
  $servicePrincipalActual = $servicePrincipalJson | ConvertFrom-Json
  $credentialsJson = & az ad app credential list --only-show-errors --id $ResumeClientId --output json
  if ($LASTEXITCODE -ne 0 -or -not $credentialsJson) { throw 'Resume credential actualを読み取れません。' }
  $assignmentsJson = & az rest --only-show-errors --method get --uri "https://graph.microsoft.com/v1.0/users/$AllowedUserObjectId/appRoleAssignments" --query 'value[].{resourceId:resourceId,principalId:principalId}' --output json
  if ($LASTEXITCODE -ne 0 -or -not $assignmentsJson) { throw 'Resume assignment actualを読み取れません。' }
  $authJson = & az containerapp auth show --only-show-errors --resource-group $ResourceGroupName --name $AppName --output json
  if ($LASTEXITCODE -ne 0 -or -not $authJson) { throw 'Resume auth actualを読み取れません。' }
  if ($actualTenantId -ine $ResumeTenantId) { throw 'Resume tenantが現在のsubscription contextと一致しません。' }
  $resumeActualFile = [System.IO.Path]::GetTempFileName()
  try {
    @{
      application = $applicationJson | ConvertFrom-Json
      servicePrincipal = $servicePrincipalActual
      credentials = @($credentialsJson | ConvertFrom-Json)
      assignments = @($assignmentsJson | ConvertFrom-Json)
      auth = $authJson | ConvertFrom-Json
    } | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $resumeActualFile -Encoding utf8NoBOM
    $resumeGuardParameters = @{
      ActualPath = $resumeActualFile
      AppName = $AppName
      ExpectedRedirectUri = $redirectUri
      ClientId = $ResumeClientId
      ServicePrincipalObjectId = $ResumeServicePrincipalObjectId
      AllowedUserObjectId = $AllowedUserObjectId
    }
    if ($ResumeOrphanCredentialKeyId -or $ResumeOrphanCredentialDisplayName) {
      $resumeGuardParameters.OrphanCredentialKeyId = $ResumeOrphanCredentialKeyId
      $resumeGuardParameters.OrphanCredentialDisplayName = $ResumeOrphanCredentialDisplayName
    }
    & "$PSScriptRoot/Assert-EntraResumeState.ps1" @resumeGuardParameters
  } finally {
    Remove-Item -LiteralPath $resumeActualFile -Force -ErrorAction SilentlyContinue
  }
  $clientId = $ResumeClientId
  $servicePrincipalId = $ResumeServicePrincipalObjectId
  $servicePrincipalAssignmentRequired = [bool]$servicePrincipalActual.appRoleAssignmentRequired
  if ($ResumeOrphanCredentialKeyId) {
    & az ad app credential delete --only-show-errors --id $clientId --key-id $ResumeOrphanCredentialKeyId
    if ($LASTEXITCODE -ne 0) { throw 'Orphan credential deletion failed or is unknown. New credential was not created.' }
    $remainingCredentialsJson = & az ad app credential list --only-show-errors --id $clientId --query '[].{keyId:keyId}' --output json
    if ($LASTEXITCODE -ne 0 -or -not $remainingCredentialsJson) { throw 'Credential actual after deletion is unknown. New credential was not created.' }
    $remainingCredentials = @($remainingCredentialsJson | ConvertFrom-Json)
    if ($remainingCredentials.Count -ne 0) { throw 'Credential remains after exact deletion. New credential was not created.' }
  }
} else {
  if ($existing) { throw "同名のapp registrationが既にあります。重複作成せずactualを確認してください: $displayName" }
  $clientId = & az ad app create --only-show-errors --display-name $displayName --sign-in-audience AzureADMyOrg --enable-id-token-issuance true --web-redirect-uris $redirectUri --query appId --output tsv
  if ($LASTEXITCODE -ne 0 -or -not $clientId) { throw "Entra app registration creation failed." }
  $servicePrincipalId = & az ad sp create --only-show-errors --id $clientId --query id --output tsv
  if ($LASTEXITCODE -ne 0 -or -not $servicePrincipalId) { throw "Entra service principal creation failed." }
}
if (-not $ResumeOrphanCredentialKeyId -and -not $servicePrincipalAssignmentRequired) {
  & az ad sp update --only-show-errors --id $servicePrincipalId --set appRoleAssignmentRequired=true --output none
  if ($LASTEXITCODE -ne 0) { throw "Entra assignment requirement update failed." }
}

$credentialDisplayName = "agent-world-easy-auth-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$credentialKeyId = $null
$credentialExpiresOn = $null
$clientSecret = $null
try {
  $credentialJson = & az ad app credential reset --only-show-errors --id $clientId --display-name $credentialDisplayName --years 1 --append --output json
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
  $allCredentials = @($credentialMetadataJson | ConvertFrom-Json)
  $matchingCredentials = @($allCredentials | Where-Object displayName -eq $credentialDisplayName)
  if ($allCredentials.Count -ne 1 -or $matchingCredentials.Count -ne 1) {
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
$keyVaultsJson = & az keyvault list --only-show-errors --resource-group $ResourceGroupName --output json
if ($LASTEXITCODE -ne 0 -or -not $keyVaultsJson) { throw "Bicep managed Key Vault actualを読み取れません。" }
try {
  $keyVaultDocument = [System.Text.Json.JsonDocument]::Parse($keyVaultsJson)
  if ($keyVaultDocument.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Array) {
    throw "Bicep managed Key Vault JSON root must be an array."
  }
} finally {
  if ($keyVaultDocument) { $keyVaultDocument.Dispose() }
}
$keyVaults = @($keyVaultsJson | ConvertFrom-Json | Where-Object { $_.tags.application -ceq 'agent-world' })
if ($keyVaults.Count -ne 1 -or -not $keyVaults[0].name) { throw "Bicep managed Key Vaultがexact 1件ではありません。" }
$keyVaultName = $keyVaults[0].name
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
$tenantId = & az account show --query tenantId --output tsv
& "$PSScriptRoot/Complete-EntraConfiguration.ps1" `
  -SubscriptionId $SubscriptionId `
  -ResourceGroupName $ResourceGroupName `
  -AppName $AppName `
  -KeyVaultName $keyVaultName `
  -TenantId $tenantId `
  -ClientId $clientId `
  -ServicePrincipalObjectId $servicePrincipalId `
  -AllowedUserObjectId $AllowedUserObjectId
Write-Output "Entra configured: clientId=$clientId allowedUserObjectId=$AllowedUserObjectId redirectUri=$redirectUri"
Write-Output "Credential metadata: keyId=$credentialKeyId displayName=$credentialDisplayName expiresOn=$credentialExpiresOn"
Write-Output "Client secret value was stored only in Key Vault and was not printed. Rotate before expiry."
