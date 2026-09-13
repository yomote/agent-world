[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $Location,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [string] $TenantId,
  [Parameter(Mandatory)] [string] $EntraClientId,
  [Parameter(Mandatory)] [string] $AllowedUserObjectId,
  [Parameter(Mandatory)] [ValidatePattern('^ghcr\.io/.+@sha256:[0-9a-f]{64}$')] [string] $Image,
  [Parameter(Mandatory)] [string] $EvidenceDirectory,
  [string] $AzureCli = 'az',
  [string] $SmokeScript = "$PSScriptRoot/Test-AzureSmoke.ps1"
)

$ErrorActionPreference = 'Stop'
function Invoke-AzJson([string[]] $Arguments, [string] $Label) {
  $json = & $AzureCli @Arguments --only-show-errors --output json
  if ($LASTEXITCODE -ne 0 -or -not $json) { throw "$Label actualを読み取れません。" }
  try { return $json | ConvertFrom-Json } catch { throw "$Label JSON is unknown." }
}
function Test-ExactCallback($Application, [string] $Expected) {
  return @($Application.web.redirectUris).Count -eq 1 -and $Application.web.redirectUris[0] -ceq $Expected
}
function Get-CanonicalJson($Value) {
  return $Value | ConvertTo-Json -Depth 100 -Compress
}
function Get-ApplicationWithoutCallbacks($Application) {
  $copy = (Get-CanonicalJson $Application) | ConvertFrom-Json
  $copy.web.redirectUris = @()
  return Get-CanonicalJson $copy
}
function Test-TerminalProvisioningState($Container) {
  return $Container.properties.provisioningState -in @('Succeeded', 'Failed', 'Canceled')
}
function Get-ImmutableContainerState($Container) {
  $ingress = $Container.properties.configuration.ingress
  return [ordered]@{
    identity = $Container.identity
    tags = $Container.tags
    environmentId = $Container.properties.environmentId
    workloadProfileName = $Container.properties.workloadProfileName
    configuration = [ordered]@{
      activeRevisionsMode = $Container.properties.configuration.activeRevisionsMode
      maxInactiveRevisions = $Container.properties.configuration.maxInactiveRevisions
      dapr = $Container.properties.configuration.dapr
      registries = $Container.properties.configuration.registries
      secrets = $Container.properties.configuration.secrets
      service = $Container.properties.configuration.service
      ingress = [ordered]@{
        allowInsecure = $ingress.allowInsecure
        targetPort = $ingress.targetPort
        transport = $ingress.transport
        traffic = $ingress.traffic
        exposedPort = $ingress.exposedPort
        additionalPortMappings = $ingress.additionalPortMappings
        clientCertificateMode = $ingress.clientCertificateMode
        customDomains = $ingress.customDomains
        ipSecurityRestrictions = $ingress.ipSecurityRestrictions
        stickySessions = $ingress.stickySessions
      }
    }
    template = $Container.properties.template
  } | ConvertTo-Json -Depth 100 -Compress
}
function Assert-AuthActual($Auth) {
  $principals = @($Auth.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.identities)
  if ($Auth.platform.enabled -isnot [bool] -or -not $Auth.platform.enabled `
      -or $Auth.globalValidation.unauthenticatedClientAction -cne 'RedirectToLoginPage' `
      -or $Auth.globalValidation.redirectToProvider -cne 'azureactivedirectory' `
      -or (Compare-Object @('/healthz') @($Auth.globalValidation.excludedPaths)) `
      -or $Auth.httpSettings.requireHttps -isnot [bool] -or -not $Auth.httpSettings.requireHttps `
      -or $Auth.login.tokenStore.enabled -isnot [bool] -or $Auth.login.tokenStore.enabled `
      -or $Auth.identityProviders.azureActiveDirectory.enabled -isnot [bool] `
      -or -not $Auth.identityProviders.azureActiveDirectory.enabled `
      -or $Auth.identityProviders.azureActiveDirectory.registration.clientId -ine $EntraClientId `
      -or $Auth.identityProviders.azureActiveDirectory.registration.openIdIssuer -cne "https://login.microsoftonline.com/$TenantId/v2.0" `
      -or $Auth.identityProviders.azureActiveDirectory.registration.clientSecretSettingName -cne 'microsoft-provider-authentication-secret' `
      -or $principals.Count -ne 1 -or $principals[0] -ine $AllowedUserObjectId) {
    throw 'External ingress前のEntra auth actualが本人限定の期待値と一致しません。'
  }
  function Test-EnabledProvider($Value) {
    if ($null -eq $Value) { return $false }
    if ($Value -is [System.Array]) { return [bool]($Value | Where-Object { Test-EnabledProvider $_ } | Select-Object -First 1) }
    if ($Value -is [pscustomobject]) {
      if ($Value.PSObject.Properties.Name -contains 'enabled' -and $Value.enabled -eq $true) { return $true }
      foreach ($property in $Value.PSObject.Properties) {
        if (Test-EnabledProvider $property.Value) { return $true }
      }
    }
    return $false
  }
  foreach ($provider in $Auth.identityProviders.PSObject.Properties) {
    if ($provider.Name -cne 'azureActiveDirectory' -and (Test-EnabledProvider $provider.Value)) {
      throw "External ingress前に想定外のidentity providerが有効です: $($provider.Name)"
    }
  }
}
function Assert-ApprovedContainerBaseline($Container, [string] $ExpectedIdentity, [string] $ExpectedEnvironment) {
  $containers = @($Container.properties.template.containers)
  $probes = if ($containers.Count -eq 1) { @($containers[0].probes) } else { @() }
  $scale = $Container.properties.template.scale
  $rules = @($scale.rules)
  $identityProperties = @($Container.identity.userAssignedIdentities.PSObject.Properties)
  if ($Container.properties.provisioningState -cne 'Succeeded' `
      -or $Container.tags.application -cne 'agent-world' -or $Container.tags.managedBy -cne 'bicep' `
      -or $Container.identity.type -cne 'UserAssigned' `
      -or $identityProperties.Count -ne 1 -or $identityProperties[0].Name -ine $ExpectedIdentity `
      -or $Container.properties.environmentId -ine $ExpectedEnvironment `
      -or $Container.properties.workloadProfileName -cne 'Consumption' `
      -or $Container.properties.configuration.activeRevisionsMode -cne 'Single' `
      -or $Container.properties.configuration.maxInactiveRevisions -ne 1 `
      -or $containers.Count -ne 1 -or $containers[0].name -cne 'agent-world' `
      -or [decimal]$containers[0].resources.cpu -ne [decimal]0.25 `
      -or $containers[0].resources.memory -cne '0.5Gi' `
      -or $probes.Count -ne 3 `
      -or $scale.minReplicas -ne 0 -or $scale.maxReplicas -ne 1 `
      -or $rules.Count -ne 1 -or $rules[0].name -cne 'http' `
      -or $rules[0].http.metadata.concurrentRequests -cne '10') {
    throw '公開前Container App baselineが承認済みsingle-worker構成と一致しません。'
  }
  $expectedProbes = @(
    @{ type = 'Startup'; period = 2; timeout = 2; failure = 30; delay = 1 },
    @{ type = 'Liveness'; period = 30; timeout = 3; failure = 3; delay = $null },
    @{ type = 'Readiness'; period = 10; timeout = 3; failure = 3; delay = $null }
  )
  foreach ($expected in $expectedProbes) {
    $matches = @($probes | Where-Object { $_.type -ceq $expected.type })
    if ($matches.Count -ne 1) { throw '公開前Container App probeが承認済み構成と一致しません。' }
    $actual = $matches[0]
    if ($actual.type -cne $expected.type `
        -or $actual.httpGet.path -cne '/healthz' -or $actual.httpGet.port -ne 8000 `
        -or $actual.httpGet.scheme -cne 'HTTP' `
        -or $actual.periodSeconds -ne $expected.period `
        -or $actual.timeoutSeconds -ne $expected.timeout `
        -or $actual.failureThreshold -ne $expected.failure `
        -or ($null -ne $expected.delay -and $actual.initialDelaySeconds -ne $expected.delay)) {
      throw '公開前Container App probeが承認済み構成と一致しません。'
    }
  }
}
function Set-RedirectUri([string] $Callback) {
  & $AzureCli ad app update --only-show-errors --id $EntraClientId --web-redirect-uris $Callback --output none
  $writeExit = $LASTEXITCODE
  try {
    $actual = Invoke-AzJson @('ad', 'app', 'show', '--id', $EntraClientId) 'Entra callback'
  } catch {
    throw "EXTERNAL PUBLICATION UNKNOWN: Entra callback actual could not be read after writeExit=$writeExit. No retry was attempted."
  }
  if (-not (Test-ExactCallback $actual $Callback)) {
    throw "EXTERNAL PUBLICATION UNKNOWN: Entra callback actual did not match the requested exact URI after writeExit=$writeExit. No retry was attempted."
  }
}
function Restore-Internal([string] $InternalFqdn, [string] $InternalCallback, [string] $Reason) {
  & $AzureCli containerapp ingress enable --only-show-errors --resource-group $ResourceGroupName --name $AppName --type internal --allow-insecure false --target-port 8000 --transport auto --output none
  $ingressExit = $LASTEXITCODE
  $actual = Invoke-AzJson @('containerapp', 'show', '--resource-group', $ResourceGroupName, '--name', $AppName) 'Containment'
  $external = $actual.properties.configuration.ingress.external
  if (-not (Test-TerminalProvisioningState $actual) `
      -or $external -isnot [bool] -or $external `
      -or $actual.properties.configuration.ingress.fqdn -ine $InternalFqdn) {
    throw "PUBLICATION FAILURE: $Reason; ingress containment is unknown (writeExit=$ingressExit)."
  }
  Set-RedirectUri $InternalCallback
  throw "PUBLICATION FAILURE: $Reason; internal ingress and callback were restored."
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$allowedEvidenceRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot 'artifacts'))
$evidenceFullPath = [IO.Path]::GetFullPath($EvidenceDirectory)
$allowedPrefix = $allowedEvidenceRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $evidenceFullPath.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'EvidenceDirectoryはrepositoryのprivate/ignored artifacts配下に限定してください。'
}
New-Item -ItemType Directory -Force -Path $evidenceFullPath | Out-Null
$EvidenceDirectory = $evidenceFullPath
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
& "$PSScriptRoot/Test-EntraDirectory.ps1" `
  -SubscriptionId $SubscriptionId `
  -ResourceGroupName $ResourceGroupName `
  -AppName $AppName `
  -TenantId $TenantId `
  -EntraClientId $EntraClientId `
  -AllowedPrincipalObjectIds @($AllowedUserObjectId) `
  -AzureCli $AzureCli

$beforeApp = Invoke-AzJson @('ad', 'app', 'show', '--id', $EntraClientId) 'Entra application'
$beforeAuth = Invoke-AzJson @('containerapp', 'auth', 'show', '--resource-group', $ResourceGroupName, '--name', $AppName) 'Easy Auth'
$beforeContainer = Invoke-AzJson @('containerapp', 'show', '--resource-group', $ResourceGroupName, '--name', $AppName) 'Container App'
Assert-AuthActual $beforeAuth
$beforeAuthCanonical = Get-CanonicalJson $beforeAuth
$beforeApplicationCanonical = Get-ApplicationWithoutCallbacks $beforeApp
$ingress = $beforeContainer.properties.configuration.ingress
if ($ingress.external -isnot [bool] -or $ingress.external `
    -or $ingress.targetPort -ne 8000 -or $ingress.transport -ine 'auto' `
    -or $ingress.allowInsecure -isnot [bool] -or $ingress.allowInsecure) {
  throw '公開前ingress actualがinternal/8000/auto/HTTPSの期待値と一致しません。'
}
$internalFqdn = [string]$ingress.fqdn
$pattern = '^' + [regex]::Escape($AppName) + '\.internal\.(?<environment>[a-z0-9-]+)\.(?<region>[a-z0-9-]+)\.azurecontainerapps\.io$'
$fqdnMatch = [regex]::Match($internalFqdn, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)
if (-not $fqdnMatch.Success `
    -or $fqdnMatch.Groups['region'].Value -ine $Location `
    -or ([regex]::Matches($internalFqdn, '\.internal\.', [Text.RegularExpressions.RegexOptions]::IgnoreCase)).Count -ne 1) {
  throw 'Internal FQDNは承認済み6-label形式のexact 1件ではありません。'
}
$publicFqdn = "$AppName.$($fqdnMatch.Groups['environment'].Value).$($fqdnMatch.Groups['region'].Value).azurecontainerapps.io"
$internalCallback = "https://$internalFqdn/.auth/login/aad/callback"
$publicCallback = "https://$publicFqdn/.auth/login/aad/callback"
if (-not (Test-ExactCallback $beforeApp $internalCallback)) { throw '公開前callbackがinternal FQDN exact 1件ではありません。' }
$containers = @($beforeContainer.properties.template.containers)
$identityProperties = @($beforeContainer.identity.userAssignedIdentities.PSObject.Properties)
$secrets = @($beforeContainer.properties.configuration.secrets)
$expectedIdentity = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.ManagedIdentity/userAssignedIdentities/$AppName-identity"
$expectedEnvironment = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.App/managedEnvironments/$AppName-env"
Assert-ApprovedContainerBaseline $beforeContainer $expectedIdentity $expectedEnvironment
$secretUri = if ($secrets.Count -eq 1) { $secrets[0].keyVaultUrl -as [Uri] } else { $null }
if ($containers.Count -ne 1 -or $containers[0].image -cne $Image `
    -or $beforeContainer.properties.environmentId -ine $expectedEnvironment `
    -or $identityProperties.Count -ne 1 -or $identityProperties[0].Name -ine $expectedIdentity `
    -or $secrets.Count -ne 1 -or $secrets[0].name -cne 'microsoft-provider-authentication-secret' `
    -or $secrets[0].identity -ine $expectedIdentity `
    -or $null -eq $secretUri -or $secretUri.Scheme -cne 'https' `
    -or $secretUri.Host -notmatch '\.vault\.azure\.net$' `
    -or $secretUri.AbsolutePath -cne '/secrets/easy-auth-client-secret' `
    -or $secretUri.Query) {
  throw '公開前Container Appのimage/UAMI/secret referenceが承認済みactualと一致しません。'
}
$vaultName = $secretUri.Host -replace '\.vault\.azure\.net$', ''
$vaultsJson = & $AzureCli keyvault list --only-show-errors --resource-group $ResourceGroupName --output json
if ($LASTEXITCODE -ne 0 -or -not $vaultsJson) { throw '公開前Key Vault actualを読み取れません。' }
try {
  $vaultDocument = [System.Text.Json.JsonDocument]::Parse($vaultsJson)
  if ($vaultDocument.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Array) {
    throw '公開前Key Vault JSON root must be an array.'
  }
} finally {
  if ($vaultDocument) { $vaultDocument.Dispose() }
}
$vaults = @($vaultsJson | ConvertFrom-Json | Where-Object { $_.tags.application -ceq 'agent-world' })
$expectedVaultId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.KeyVault/vaults/$vaultName"
if ($vaults.Count -ne 1 -or $vaults[0].name -cne $vaultName -or $vaults[0].id -ine $expectedVaultId) {
  throw '公開前secret URLは対象RGのagent-world Key Vault exact 1件と一致しません。'
}
$beforeComparable = Get-ImmutableContainerState $beforeContainer
@{ application = $beforeApp; auth = $beforeAuth; container = $beforeContainer; expectedPublicFqdn = $publicFqdn } |
  ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'external-before.private.json') -Encoding utf8NoBOM

Set-RedirectUri $publicCallback
& $AzureCli containerapp ingress enable --only-show-errors --resource-group $ResourceGroupName --name $AppName --type external --allow-insecure false --target-port 8000 --transport auto --output none
$enableExit = $LASTEXITCODE
try {
  $afterContainer = Invoke-AzJson @('containerapp', 'show', '--resource-group', $ResourceGroupName, '--name', $AppName) 'Published Container App'
} catch {
  throw "EXTERNAL PUBLICATION UNKNOWN: ingress actual could not be read after writeExit=$enableExit. No retry was attempted."
}
$provisioningState = $afterContainer.properties.provisioningState
if (-not (Test-TerminalProvisioningState $afterContainer)) {
  throw "EXTERNAL PUBLICATION UNKNOWN: provisioningState=$provisioningState may still change ingress later. No retry was attempted."
}
$afterIngress = $afterContainer.properties.configuration.ingress
if ($afterIngress.external -is [bool] -and -not $afterIngress.external -and $afterIngress.fqdn -ieq $internalFqdn) {
  Set-RedirectUri $internalCallback
  throw "PUBLICATION FAILURE: external ingress write did not take effect (writeExit=$enableExit); ingress stayed internal and callback was restored."
}
if ($afterIngress.external -isnot [bool] -or -not $afterIngress.external -or $afterIngress.fqdn -ine $publicFqdn) {
  if ($afterIngress.external -isnot [bool]) {
    throw "EXTERNAL PUBLICATION UNKNOWN: terminal ingress external actual is not boolean. No retry was attempted."
  }
  Restore-Internal $internalFqdn $internalCallback "external ingress actual mismatch (writeExit=$enableExit)"
}
if ($provisioningState -cne 'Succeeded') {
  Restore-Internal $internalFqdn $internalCallback "external ingress provisioning ended in $provisioningState (writeExit=$enableExit)"
}
try {
  $afterAuth = Invoke-AzJson @('containerapp', 'auth', 'show', '--resource-group', $ResourceGroupName, '--name', $AppName) 'Published Easy Auth'
  $afterApp = Invoke-AzJson @('ad', 'app', 'show', '--id', $EntraClientId) 'Published Entra application'
  Assert-AuthActual $afterAuth
  if (-not (Test-ExactCallback $afterApp $publicCallback)) { throw 'public callback actual mismatch' }
  if ((Get-CanonicalJson $afterAuth) -cne $beforeAuthCanonical) { throw 'Easy Auth actual drift' }
  if ((Get-ApplicationWithoutCallbacks $afterApp) -cne $beforeApplicationCanonical) { throw 'Entra application actual drift' }
  & "$PSScriptRoot/Test-EntraDirectory.ps1" `
    -SubscriptionId $SubscriptionId `
    -ResourceGroupName $ResourceGroupName `
    -AppName $AppName `
    -TenantId $TenantId `
    -EntraClientId $EntraClientId `
    -AllowedPrincipalObjectIds @($AllowedUserObjectId) `
    -AzureCli $AzureCli
  if ((Get-ImmutableContainerState $afterContainer) -cne $beforeComparable) { throw 'Container App immutable state drift' }
  & $SmokeScript -BaseUrl "https://$publicFqdn" -AuthMode entra
} catch {
  Restore-Internal $internalFqdn $internalCallback $_.Exception.Message
}
@{ application = $afterApp; auth = $afterAuth; container = $afterContainer } |
  ConvertTo-Json -Depth 100 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory 'external-after.private.json') -Encoding utf8NoBOM
Write-Output "External ingress publication verified: https://$publicFqdn"
