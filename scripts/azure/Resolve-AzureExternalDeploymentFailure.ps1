[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [Parameter(Mandatory)] [string] $AppName,
  [string] $AzureCli = 'az'
)

$ErrorActionPreference = 'Stop'
$external = & $AzureCli containerapp show `
  --only-show-errors `
  --resource-group $ResourceGroupName `
  --name $AppName `
  --query 'properties.configuration.ingress.external' `
  --output tsv
if ($LASTEXITCODE -ne 0 -or $external -notin @('true', 'false')) {
  throw 'EXTERNAL DEPLOYMENT UNKNOWN: deployment was not retried and ingress actual could not be verified.'
}
if ($external -eq 'true') {
  try {
    & "$PSScriptRoot/Disable-AzureExternalIngress.ps1" `
      -ResourceGroupName $ResourceGroupName `
      -AppName $AppName `
      -AzureCli $AzureCli
  } catch {
    throw "EXTERNAL DEPLOYMENT UNKNOWN: deployment was not retried; containment failed or is unknown ($($_.Exception.Message))."
  }
  throw 'EXTERNAL DEPLOYMENT FAILED: deployment was not retried; external ingress containment was verified.'
}

throw 'EXTERNAL DEPLOYMENT FAILED: deployment was not retried; ingress actual remained internal.'
