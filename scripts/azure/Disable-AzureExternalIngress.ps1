[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [string] $AzureCli = 'az'
)

$ErrorActionPreference = 'Stop'
& $AzureCli containerapp ingress disable `
  --only-show-errors `
  --resource-group $ResourceGroupName `
  --name $AppName `
  --output none
$disableExit = $LASTEXITCODE

$external = & $AzureCli containerapp show `
  --only-show-errors `
  --resource-group $ResourceGroupName `
  --name $AppName `
  --query 'properties.configuration.ingress.external' `
  --output tsv
if ($LASTEXITCODE -ne 0) {
  throw 'External ingress containment result is unknown; the write was not retried.'
}
if ($external -ne 'false') {
  throw 'External ingress containment is still public or unknown and was not retried.'
}

$result = if ($disableExit -eq 0) { 'succeeded' } else { 'write failed but actual is contained' }
Write-Output "External ingress containment verified: $result."
