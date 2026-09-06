[CmdletBinding()]
param(
  [string] $Repository = "yomote/agent-world",
  [string] $Environment = "azure-production"
)

$ErrorActionPreference = "Stop"
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "Repository must be OWNER/REPO." }
$credentialInput = "protocol=https`nhost=github.com`n`n"
$credentialLines = $credentialInput | git credential fill
$credential = @{}
foreach ($line in $credentialLines) {
  if ($line -match '^([^=]+)=(.*)$') { $credential[$matches[1]] = $matches[2] }
}
if (-not $credential.password) { throw "GitHub credential was not available from GCM." }
$headers = @{
  Accept = 'application/vnd.github+json'
  Authorization = "Bearer $($credential.password)"
  'X-GitHub-Api-Version' = '2022-11-28'
  'User-Agent' = 'agent-world-azure-bootstrap'
}
$base = "https://api.github.com/repos/$Repository/environments/$Environment"
try {
  $actual = Invoke-RestMethod -Method Get -Uri $base -Headers $headers
  $actualPolicies = Invoke-RestMethod -Method Get -Uri "$base/deployment-branch-policies" -Headers $headers
  $hasAdminBypass = $actual.PSObject.Properties.Name -contains 'can_admins_bypass'
  if (-not $hasAdminBypass `
      -or $actual.can_admins_bypass -isnot [bool] `
      -or $actual.can_admins_bypass -ne $false `
      -or -not $actual.deployment_branch_policy.custom_branch_policies `
      -or $actual.deployment_branch_policy.protected_branches `
      -or @($actualPolicies.branch_policies).Count -ne 1 `
      -or $actualPolicies.branch_policies[0].name -ne 'main' `
      -or $actualPolicies.branch_policies[0].type -ne 'branch') {
    throw "GitHub environment actualはadmin bypass無効・main限定として検証できません。infra/githubを適用しactualを確認してください。"
  }
} finally {
  $credential.Clear()
  Remove-Variable credentialLines -ErrorAction SilentlyContinue
}
Write-Output "GitHub environment verified: $Environment has no admin bypass and allows deployments from main only."
