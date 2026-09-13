[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $ActualPath,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [string] $ExpectedRedirectUri,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ClientId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ServicePrincipalObjectId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $AllowedUserObjectId,
  [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $OrphanCredentialKeyId = '',
  [string] $OrphanCredentialDisplayName = ''
)

$ErrorActionPreference = 'Stop'
$actual = Get-Content -Raw -LiteralPath $ActualPath | ConvertFrom-Json
$redirects = @($actual.application.web.redirectUris)
$credentials = @($actual.credentials)
$orphanStage = $OrphanCredentialKeyId -or $OrphanCredentialDisplayName
if ([bool]$OrphanCredentialKeyId -ne [bool]$OrphanCredentialDisplayName) {
  throw 'Orphan credential key IDとdisplay nameは両方必要です。'
}
$assignmentRequiredProperty = $actual.servicePrincipal.PSObject.Properties['appRoleAssignmentRequired']
$assignmentRequiredIsKnown = $null -ne $assignmentRequiredProperty `
  -and $assignmentRequiredProperty.Value -is [bool]
$assignmentRequired = if ($assignmentRequiredIsKnown) {
  [bool]$assignmentRequiredProperty.Value
} else {
  $false
}
$assignmentsHaveValidShape = $actual.PSObject.Properties.Name -contains 'assignments' -and $actual.assignments -is [System.Array]
if ($assignmentsHaveValidShape) {
  foreach ($assignment in $actual.assignments) {
    $resourceId = [Guid]::Empty
    $principalId = [Guid]::Empty
    if ($assignment -isnot [pscustomobject] `
        -or -not [Guid]::TryParse([string]$assignment.resourceId, [ref]$resourceId) `
        -or -not [Guid]::TryParse([string]$assignment.principalId, [ref]$principalId)) {
      $assignmentsHaveValidShape = $false
      break
    }
  }
}
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
    -or -not $assignmentRequiredIsKnown `
    -or ($orphanStage -and -not $assignmentRequired) `
    -or ($orphanStage -and ($credentials.Count -ne 1 `
        -or $credentials[0].keyId -ine $OrphanCredentialKeyId `
        -or $credentials[0].displayName -cne $OrphanCredentialDisplayName `
        -or -not $credentials[0].endDateTime)) `
    -or (-not $orphanStage -and $credentials.Count -ne 0) `
    -or -not $assignmentsHaveValidShape `
    -or $assignments.Count -ne 0 `
    -or $actual.auth.platform.enabled `
    -or $actual.auth.identityProviders.azureActiveDirectory.enabled) {
  throw 'Entra resume actualが既知のapp/SP作成直後stateと一致しません。再作成やcredential発行は行いません。'
}

Write-Output 'Entra partial app/SP state matches the approved resume point.'
