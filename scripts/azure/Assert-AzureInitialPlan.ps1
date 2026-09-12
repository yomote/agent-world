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
  throw 'Azure initial what-if did not report Succeeded.'
}

$base = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName"
$patterns = [ordered]@{
  resourceGroup = "^$([regex]::Escape($base))$"
  containerApp = "^$([regex]::Escape($base))/providers/Microsoft\.App/containerApps/$([regex]::Escape($AppName))$"
  environment = "^$([regex]::Escape($base))/providers/Microsoft\.App/managedEnvironments/$([regex]::Escape("$AppName-env"))$"
  keyVault = "^$([regex]::Escape($base))/providers/Microsoft\.KeyVault/vaults/aw[a-z0-9]{3,22}$"
  keyVaultRole = "^$([regex]::Escape($base))/providers/Microsoft\.KeyVault/vaults/aw[a-z0-9]{3,22}/providers/Microsoft\.Authorization/roleAssignments/[0-9a-fA-F-]{36}$"
  identity = "^$([regex]::Escape($base))/providers/Microsoft\.ManagedIdentity/userAssignedIdentities/$([regex]::Escape("$AppName-identity"))$"
  workspace = "^$([regex]::Escape($base))/providers/Microsoft\.OperationalInsights/workspaces/$([regex]::Escape("$AppName-logs"))$"
  budget = "^$([regex]::Escape($base))/providers/Microsoft\.Consumption/budgets/$([regex]::Escape("$AppName-monthly"))$"
}
$expected = @{
  resourceGroup = 1
  containerApp = 1
  environment = 1
  keyVault = 1
  keyVaultRole = 2
  identity = 1
  workspace = 1
  budget = 1
}
$actual = @{}
foreach ($key in $expected.Keys) {
  $actual[$key] = 0
}

$changes = @($plan.changes)
foreach ($change in $changes) {
  if ($change.changeType -ne 'Create') {
    throw "Azure initial what-if contains non-Create change: $($change.changeType)."
  }
  $matched = $false
  foreach ($entry in $patterns.GetEnumerator()) {
    if ($change.resourceId -match $entry.Value) {
      $actual[$entry.Key]++
      $matched = $true
      break
    }
  }
  if (-not $matched) {
    throw 'Azure initial what-if contains a resource outside the approved allowlist.'
  }
}

foreach ($key in $expected.Keys) {
  if ($actual[$key] -ne $expected[$key]) {
    throw "Azure initial what-if count mismatch for ${key}: expected $($expected[$key]), actual $($actual[$key])."
  }
}

Write-Output 'Azure initial what-if is Create-only and matches the approved resource allowlist.'
