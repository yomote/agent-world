[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $TenantId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ClientId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ServicePrincipalObjectId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $AllowedUserObjectId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $CredentialKeyId,
  [Parameter(Mandatory)] [string] $CredentialDisplayName,
  [Parameter(Mandatory)] [string] $CredentialExpiresOn,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]+$')] [string] $KeyVaultSecretVersion
)

$ErrorActionPreference = 'Stop'
function Assert-JsonRootArray([string] $Json, [string] $Label) {
  try {
    $document = [System.Text.Json.JsonDocument]::Parse($Json)
    if ($document.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Array) {
      throw "$Label JSON root must be an array."
    }
  } catch {
    throw "$Label JSON root is unknown: $($_.Exception.Message)"
  } finally {
    if ($document) { $document.Dispose() }
  }
}
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$accountType = & az account show --only-show-errors --query user.type --output tsv
if ($LASTEXITCODE -ne 0 -or -not $accountType) { throw 'Entra account type actualを読み取れません。' }
$actualTenantId = & az account show --only-show-errors --query tenantId --output tsv
if ($LASTEXITCODE -ne 0 -or -not $actualTenantId) { throw 'Entra tenant actualを読み取れません。' }
$signedInUserId = & az ad signed-in-user show --only-show-errors --query id --output tsv
if ($LASTEXITCODE -ne 0 -or $accountType -ne 'user' -or $actualTenantId -ine $TenantId -or $signedInUserId -ine $AllowedUserObjectId) {
  throw 'Entra Key Vault後resumeのtenantまたは本人contextが一致しません。'
}

$appIds = @(& az ad app list --only-show-errors --display-name "$AppName-login" --query '[].appId' --output tsv)
if ($LASTEXITCODE -ne 0 -or $appIds.Count -ne 1 -or $appIds[0] -ine $ClientId) {
  throw 'Entra Key Vault後resume対象以外のapp registrationまたは重複appがあります。'
}
$ingressJson = & az containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query properties.configuration.ingress --output json
if ($LASTEXITCODE -ne 0 -or -not $ingressJson) { throw 'Container App ingress actualを読み取れません。' }
$applicationJson = & az ad app show --only-show-errors --id $ClientId --output json
if ($LASTEXITCODE -ne 0 -or -not $applicationJson) { throw 'Entra application actualを読み取れません。' }
$servicePrincipalJson = & az ad sp show --only-show-errors --id $ClientId --output json
if ($LASTEXITCODE -ne 0 -or -not $servicePrincipalJson) { throw 'Entra service principal actualを読み取れません。' }
$credentialsJson = & az ad app credential list --only-show-errors --id $ClientId --output json
if ($LASTEXITCODE -ne 0 -or -not $credentialsJson) { throw 'Entra credential actualを読み取れません。' }
Assert-JsonRootArray -Json $credentialsJson -Label 'Entra credential'
$assignmentsJson = & az rest --only-show-errors --method get --uri "https://graph.microsoft.com/v1.0/users/$AllowedUserObjectId/appRoleAssignments" --query 'value[].{resourceId:resourceId,principalId:principalId}' --output json
if ($LASTEXITCODE -ne 0 -or -not $assignmentsJson) { throw 'Entra assignment actualを読み取れません。' }
$authJson = & az containerapp auth show --only-show-errors --resource-group $ResourceGroupName --name $AppName --output json
if ($LASTEXITCODE -ne 0 -or -not $authJson) { throw 'Container App auth actualを読み取れません。' }
$ingress = $ingressJson | ConvertFrom-Json
$externalProperty = $ingress.PSObject.Properties['external']
if ($null -eq $externalProperty -or $externalProperty.Value -isnot [bool] -or $externalProperty.Value -or -not $ingress.fqdn) {
  throw 'Entra Key Vault後resume前のingressはinternalのactualが必須です。'
}

$resumeActualFile = [System.IO.Path]::GetTempFileName()
try {
  @{
    application = $applicationJson | ConvertFrom-Json
    servicePrincipal = $servicePrincipalJson | ConvertFrom-Json
    credentials = @($credentialsJson | ConvertFrom-Json)
    assignments = @($assignmentsJson | ConvertFrom-Json)
    auth = $authJson | ConvertFrom-Json
  } | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $resumeActualFile -Encoding utf8NoBOM
  & "$PSScriptRoot/Assert-EntraResumeState.ps1" `
    -ActualPath $resumeActualFile `
    -AppName $AppName `
    -ExpectedRedirectUri "https://$($ingress.fqdn)/.auth/login/aad/callback" `
    -ClientId $ClientId `
    -ServicePrincipalObjectId $ServicePrincipalObjectId `
    -AllowedUserObjectId $AllowedUserObjectId `
    -OrphanCredentialKeyId $CredentialKeyId `
    -OrphanCredentialDisplayName $CredentialDisplayName
} finally {
  Remove-Item -LiteralPath $resumeActualFile -Force -ErrorAction SilentlyContinue
}

$credentials = @($credentialsJson | ConvertFrom-Json)
$expectedExpiry = [DateTimeOffset]::MinValue
$actualExpiry = [DateTimeOffset]::MinValue
$expectedExpiryValid = [DateTimeOffset]::TryParse(
  $CredentialExpiresOn,
  [Globalization.CultureInfo]::InvariantCulture,
  [Globalization.DateTimeStyles]::RoundtripKind,
  [ref]$expectedExpiry
)
$actualExpiryValid = if ($credentials.Count -eq 1 -and $credentials[0].endDateTime -is [DateTime]) {
  $actualExpiry = [DateTimeOffset]$credentials[0].endDateTime
  $true
} elseif ($credentials.Count -eq 1) {
  [DateTimeOffset]::TryParse(
    [string]$credentials[0].endDateTime,
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::RoundtripKind,
    [ref]$actualExpiry
  )
} else {
  $false
}
if (-not $expectedExpiryValid `
    -or $credentials.Count -ne 1 `
    -or -not $actualExpiryValid `
    -or $actualExpiry -ne $expectedExpiry) {
  throw 'Entra credential expiryが承認済みmetadataと一致しません。'
}
$vaultsJson = & az keyvault list --only-show-errors --resource-group $ResourceGroupName --output json
if ($LASTEXITCODE -ne 0 -or -not $vaultsJson) { throw 'Key Vault actualを読み取れません。' }
Assert-JsonRootArray -Json $vaultsJson -Label 'Key Vault'
$vaults = @($vaultsJson | ConvertFrom-Json | Where-Object { $_.tags.application -ceq 'agent-world' })
if ($vaults.Count -ne 1 -or -not $vaults[0].name) { throw 'Key Vaultが承認済みのexact 1件ではありません。' }
$keyVaultName = $vaults[0].name
$secretMetadataJson = & az keyvault secret list-versions --only-show-errors --vault-name $keyVaultName --name easy-auth-client-secret --output json
if ($LASTEXITCODE -ne 0 -or -not $secretMetadataJson) { throw 'Key Vault secret metadataを読み取れません。' }
Assert-JsonRootArray -Json $secretMetadataJson -Label 'Key Vault secret version'
$allSecretVersions = @($secretMetadataJson | ConvertFrom-Json)
$secretMetadata = @($allSecretVersions | Where-Object name -CEQ 'easy-auth-client-secret')
$secretExpiry = [DateTimeOffset]::MinValue
$secretExpiryValid = if ($secretMetadata.Count -eq 1 -and $secretMetadata[0].tags.expiresOn -is [DateTime]) {
  $secretExpiry = [DateTimeOffset]$secretMetadata[0].tags.expiresOn
  $true
} elseif ($secretMetadata.Count -eq 1) {
  [DateTimeOffset]::TryParse(
    [string]$secretMetadata[0].tags.expiresOn,
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::RoundtripKind,
    [ref]$secretExpiry
  )
} else {
  $false
}
if ($allSecretVersions.Count -ne 1 `
    -or $secretMetadata.Count -ne 1 `
    -or $secretMetadata[0].attributes.enabled -isnot [bool] `
    -or -not $secretMetadata[0].attributes.enabled `
    -or $secretMetadata[0].tags.entraCredentialKeyId -ine $CredentialKeyId `
    -or $secretMetadata[0].tags.entraCredentialDisplayName -cne $CredentialDisplayName `
    -or -not $secretExpiryValid `
    -or $secretExpiry -ne $expectedExpiry `
    -or ([Uri]$secretMetadata[0].id).Segments[-1].Trim('/') -ine $KeyVaultSecretVersion) {
  throw 'Key Vault secret metadataが承認済みcredential/versionと一致しません。'
}
$containerSecretsJson = & az containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query properties.configuration.secrets --output json
if ($LASTEXITCODE -ne 0 -or -not $containerSecretsJson) { throw 'Container App secret reference actualを読み取れません。' }
Assert-JsonRootArray -Json $containerSecretsJson -Label 'Container App secret reference'
$targetSecret = @($containerSecretsJson | ConvertFrom-Json | Where-Object name -CEQ 'microsoft-provider-authentication-secret')
if ($targetSecret.Count -ne 0) { throw 'Container App secret referenceが既に存在します。再送しません。' }

& "$PSScriptRoot/Complete-EntraConfiguration.ps1" `
  -SubscriptionId $SubscriptionId `
  -ResourceGroupName $ResourceGroupName `
  -AppName $AppName `
  -KeyVaultName $keyVaultName `
  -TenantId $TenantId `
  -ClientId $ClientId `
  -ServicePrincipalObjectId $ServicePrincipalObjectId `
  -AllowedUserObjectId $AllowedUserObjectId
