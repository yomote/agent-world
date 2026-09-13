[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [string] $TenantId,
  [Parameter(Mandatory)] [string] $EntraClientId,
  [Parameter(Mandatory)] [string[]] $AllowedPrincipalObjectIds,
  [string] $AzureCli = 'az'
)

$ErrorActionPreference = "Stop"
function Assert-AssignmentProjection([string] $Json) {
  try {
    $document = [System.Text.Json.JsonDocument]::Parse($Json)
    if ($document.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Array) {
      throw 'root must be an array'
    }
    foreach ($item in $document.RootElement.EnumerateArray()) {
      if ($item.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) {
        throw 'each item must be an object'
      }
      $resourceId = $item.GetProperty('resourceId').GetString()
      $principalId = $item.GetProperty('principalId').GetString()
      $parsedResourceId = [Guid]::Empty
      $parsedPrincipalId = [Guid]::Empty
      if (-not [Guid]::TryParse($resourceId, [ref]$parsedResourceId) `
          -or -not [Guid]::TryParse($principalId, [ref]$parsedPrincipalId)) {
        throw 'required IDs must be GUIDs'
      }
    }
  } catch {
    throw "Entra assignment projection JSON is unknown: $($_.Exception.Message)"
  } finally {
    if ($document) { $document.Dispose() }
  }
}
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$accountType = & $AzureCli account show --only-show-errors --query user.type --output tsv
if ($accountType -ne 'user') { throw "Entra directory検査は本人のuser contextで実行してください。" }
$accountTenant = & $AzureCli account show --only-show-errors --query tenantId --output tsv
if ($accountTenant -ne $TenantId) { throw "Selected tenant does not match the expected Entra tenant." }
$signedInUserId = & $AzureCli ad signed-in-user show --only-show-errors --query id --output tsv
if ($LASTEXITCODE -ne 0 `
    -or @($AllowedPrincipalObjectIds).Count -ne 1 `
    -or $AllowedPrincipalObjectIds[0] -ne $signedInUserId) {
  throw "AllowedPrincipalObjectIdsは現在login中の本人1名と一致させてください。"
}

$app = (& $AzureCli ad app show --only-show-errors --id $EntraClientId --output json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Entra app registration was not found." }
$fqdn = & $AzureCli containerapp show --only-show-errors --resource-group $ResourceGroupName --name $AppName --query 'properties.configuration.ingress.fqdn' --output tsv
$expectedCallback = "https://$fqdn/.auth/login/aad/callback"
if ($app.signInAudience -ne 'AzureADMyOrg' `
    -or -not $app.web.implicitGrantSettings.enableIdTokenIssuance `
    -or (Compare-Object @($expectedCallback) @($app.web.redirectUris))) {
  throw "Entra app registration single-tenant, ID token, or callback drift detected."
}
$sp = (& $AzureCli ad sp show --only-show-errors --id $EntraClientId --output json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or -not $sp.appRoleAssignmentRequired) { throw "Entra service principal assignment requirement drift detected." }
foreach ($principalId in $AllowedPrincipalObjectIds) {
  # Project only IDs. Full Graph objects may contain display names that are not
  # safe to parse after Windows az.cmd has transported the response.
  $assignmentsJson = & $AzureCli rest --only-show-errors --method get --uri "https://graph.microsoft.com/v1.0/users/$principalId/appRoleAssignments" --query 'value[].{resourceId:resourceId,principalId:principalId}' --output json
  if ($LASTEXITCODE -ne 0 -or -not $assignmentsJson) {
    throw "Allowed user assignment actualを読み取れません: $principalId"
  }
  Assert-AssignmentProjection -Json $assignmentsJson
  $assignments = @($assignmentsJson | ConvertFrom-Json)
  $targetAssignments = @($assignments | Where-Object {
      $_.resourceId -ieq $sp.id -and $_.principalId -ieq $principalId
    })
  if ($targetAssignments.Count -ne 1) {
    throw "Allowed user is not assigned to the Entra enterprise app: $principalId"
  }
}
Write-Output "Entra directory registration and assignments: no drift"
