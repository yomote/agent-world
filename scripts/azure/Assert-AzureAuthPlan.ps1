[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $WhatIfPath,
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName
)

$ErrorActionPreference = 'Stop'
$plan = Get-Content -Raw -LiteralPath $WhatIfPath | ConvertFrom-Json
if ($plan.status -ne 'Succeeded') {
  throw 'Azure auth what-if did not report Succeeded.'
}

$containerAppId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.App/containerApps/$AppName"
$changes = @($plan.changes)
if ($changes.Count -ne 1) {
  throw "Azure auth what-if must contain exactly one Container App change; actual $($changes.Count)."
}
if ($changes[0].changeType -ne 'Modify' -or $changes[0].resourceId -ne $containerAppId) {
  throw 'Azure auth what-if contains a change outside the approved Container App update.'
}

Write-Output 'Azure auth what-if is limited to the approved Container App update.'
