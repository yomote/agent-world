[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $ActualPath,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [string] $ExpectedRedirectUri,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ClientId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ServicePrincipalObjectId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $AllowedUserObjectId
)

$ErrorActionPreference = 'Stop'
$actual = Get-Content -Raw -LiteralPath $ActualPath | ConvertFrom-Json
$redirects = @($actual.application.web.redirectUris)
$credentials = @($actual.credentials)
$assignments = @($actual.assignments | Where-Object {
    $_.resourceId -ieq $ServicePrincipalObjectId -and $_.principalId -ieq $AllowedUserObjectId
  })

if (($actual.application.appId -ine $ClientId) `
    -or ($actual.application.displayName -cne "$AppName-login") `
    -or ($actual.application.signInAudience -cne 'AzureADMyOrg') `
    -or $redirects.Count -ne 1 `
    -or ($redirects[0] -cne $ExpectedRedirectUri) `
    -or -not $actual.application.web.implicitGrantSettings.enableIdTokenIssuance `
    -or ($actual.servicePrincipal.id -ine $ServicePrincipalObjectId) `
    -or ($actual.servicePrincipal.appId -ine $ClientId) `
    -or ($actual.servicePrincipal.displayName -cne "$AppName-login") `
    -or ($actual.servicePrincipal.servicePrincipalType -cne 'Application') `
    -or $actual.servicePrincipal.appRoleAssignmentRequired `
    -or $credentials.Count -ne 0 `
    -or $assignments.Count -ne 0 `
    -or $actual.auth.platform.enabled `
    -or $actual.auth.identityProviders.azureActiveDirectory.enabled) {
  throw 'Entra resume actualが既知のapp/SP作成直後stateと一致しません。再作成やcredential発行は行いません。'
}

Write-Output 'Entra partial app/SP state matches the approved resume point.'
