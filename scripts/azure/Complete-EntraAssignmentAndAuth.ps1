[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $TenantId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ClientId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $ServicePrincipalObjectId,
  [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $AllowedUserObjectId
)

$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$assignmentFile = [System.IO.Path]::GetTempFileName()
try {
  @{
    principalId = $AllowedUserObjectId
    resourceId = $ServicePrincipalObjectId
    appRoleId = '00000000-0000-0000-0000-000000000000'
  } | ConvertTo-Json -Compress | Set-Content -LiteralPath $assignmentFile -Encoding utf8NoBOM
  $null = & az rest --only-show-errors --method post --uri "https://graph.microsoft.com/v1.0/users/$AllowedUserObjectId/appRoleAssignments" --headers Content-Type=application/json --body "@$assignmentFile" --output none
  if ($LASTEXITCODE -ne 0) { throw '本人のEntra app assignment failed. Auth config was not deployed.' }
} finally {
  Remove-Item -LiteralPath $assignmentFile -Force -ErrorAction SilentlyContinue
}

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
