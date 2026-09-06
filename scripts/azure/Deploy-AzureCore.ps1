[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $Location,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [ValidatePattern('^ghcr\.io/.+@sha256:[0-9a-f]{64}$')] [string] $Image,
  [Parameter(Mandatory)] [ValidatePattern('^\d{4}-\d{2}-01T00:00:00Z$')] [string] $BudgetStartDate,
  [ValidateRange(1, 1000000)] [int] $BudgetAmount = 1000,
  [string[]] $BudgetContactEmails = @(),
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $OperatorPrincipalObjectId,
  [string] $TenantId = '',
  [string] $EntraClientId = '',
  [switch] $EnableEntraAuth,
  [switch] $ExternalIngress,
  [switch] $Apply
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$accountType = & az account show --only-show-errors --query user.type --output tsv
$signedInUserId = & az ad signed-in-user show --only-show-errors --query id --output tsv
if ($accountType -ne 'user' -or $signedInUserId -ne $OperatorPrincipalObjectId) {
  throw "OperatorPrincipalObjectIdは現在login中の本人object IDと一致させてください。"
}
& "$PSScriptRoot/Test-GhcrPublic.ps1" -Image $Image
if ($ExternalIngress -and -not $EnableEntraAuth) {
  throw "ExternalIngressはEnableEntraAuthと同時にだけ有効化できます。"
}
if ($ExternalIngress) {
  if (-not $TenantId -or -not $EntraClientId) { throw "ExternalIngressにはTenantIdとEntraClientIdが必要です。" }
  & "$PSScriptRoot/Test-EntraDirectory.ps1" `
    -SubscriptionId $SubscriptionId `
    -ResourceGroupName $ResourceGroupName `
    -AppName $AppName `
    -TenantId $TenantId `
    -EntraClientId $EntraClientId `
    -AllowedPrincipalObjectIds @($OperatorPrincipalObjectId)
  $authJson = & az containerapp auth show --only-show-errors --resource-group $ResourceGroupName --name $AppName --output json
  if ($LASTEXITCODE -ne 0 -or -not $authJson) { throw "外部公開前のEntra auth actualがありません。" }
  $auth = $authJson | ConvertFrom-Json
  $actualPrincipals = @($auth.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.identities)
  if (-not $auth.platform.enabled `
      -or $auth.globalValidation.unauthenticatedClientAction -ne 'RedirectToLoginPage' `
      -or $auth.globalValidation.redirectToProvider -ne 'azureactivedirectory' `
      -or (Compare-Object @('/healthz') @($auth.globalValidation.excludedPaths)) `
      -or -not $auth.httpSettings.requireHttps `
      -or $auth.login.tokenStore.enabled `
      -or -not $auth.identityProviders.azureActiveDirectory.enabled `
      -or $auth.identityProviders.azureActiveDirectory.registration.clientId -ne $EntraClientId `
      -or $auth.identityProviders.azureActiveDirectory.registration.openIdIssuer -ne "https://login.microsoftonline.com/$TenantId/v2.0" `
      -or $auth.identityProviders.azureActiveDirectory.registration.clientSecretSettingName -ne 'microsoft-provider-authentication-secret' `
      -or $actualPrincipals.Count -ne 1 `
      -or $actualPrincipals[0] -ne $OperatorPrincipalObjectId) {
    throw "外部公開前のEntra auth actualが本人限定の期待値と一致しません。"
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
      throw "外部公開前に想定外のidentity providerが有効です: $($provider.Name)"
    }
  }
}

$parameters = @{
  resourceGroupName = @{ value = $ResourceGroupName }
  location = @{ value = $Location }
  appName = @{ value = $AppName }
  image = @{ value = $Image }
  budgetAmount = @{ value = $BudgetAmount }
  budgetStartDate = @{ value = $BudgetStartDate }
  budgetContactEmails = @{ value = $BudgetContactEmails }
  enableEntraAuth = @{ value = [bool]$EnableEntraAuth }
  externalIngress = @{ value = [bool]$ExternalIngress }
  operatorPrincipalObjectId = @{ value = $OperatorPrincipalObjectId }
}
$parameterFile = [System.IO.Path]::GetTempFileName()
try {
  @{ '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'; contentVersion = '1.0.0.0'; parameters = $parameters } |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $parameterFile -Encoding utf8NoBOM
  $common = @('--only-show-errors', '--location', $Location, '--template-file', "$PSScriptRoot/../../infra/azure/main.bicep", '--parameters', "@$parameterFile")
  & az deployment sub what-if @common --result-format FullResourcePayloads --no-pretty-print
  if ($LASTEXITCODE -ne 0) { throw "Azure subscription what-if failed; apply was not attempted." }
  if (-not $Apply) {
    Write-Output "WHAT-IF ONLY: no Azure resource was created or changed."
    return
  }
  & az deployment sub create @common --name "agent-world-core-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))" --output json
  if ($LASTEXITCODE -ne 0) { throw "Azure core deployment failed." }
  if ($ExternalIngress) {
    $fqdn = & az containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query 'properties.configuration.ingress.fqdn' --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $fqdn) { throw "公開後のContainer App FQDNを取得できません。" }
    & "$PSScriptRoot/Test-AzureSmoke.ps1" -BaseUrl "https://$fqdn" -AuthMode entra
  }
} finally {
  Remove-Item -LiteralPath $parameterFile -Force -ErrorAction SilentlyContinue
}
