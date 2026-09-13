[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string] $SubscriptionId,
  [Parameter(Mandatory)] [string] $ResourceGroupName,
  [string] $Repository = "yomote/agent-world",
  [string] $Environment = "azure-production"
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot/Assert-AzureContext.ps1" -SubscriptionId $SubscriptionId
$resourceGroupId = & az group show --only-show-errors --name $ResourceGroupName --query id --output tsv
if ($LASTEXITCODE -ne 0 -or -not $resourceGroupId) { throw "OIDC scopeにする専用Resource Groupが見つかりません。" }
$displayName = "agent-world-github-oidc"
$existing = & az ad app list --display-name $displayName --query '[].appId' --output tsv --only-show-errors
if ($LASTEXITCODE -ne 0) { throw "OIDC app registration lookup failed." }
if ($existing) { throw "同名のOIDC app registrationが既にあります。重複作成せずactualを確認してください: $displayName" }
$clientId = & az ad app create --only-show-errors --display-name $displayName --sign-in-audience AzureADMyOrg --query appId --output tsv
if ($LASTEXITCODE -ne 0 -or -not $clientId) { throw "OIDC app registration creation failed." }
$spObjectId = & az ad sp create --only-show-errors --id $clientId --query id --output tsv
if ($LASTEXITCODE -ne 0 -or -not $spObjectId) { throw "OIDC service principal creation failed." }
$credential = @{
  name = 'github-environment'
  issuer = 'https://token.actions.githubusercontent.com'
  subject = "repo:$Repository`:environment:$Environment"
  audiences = @('api://AzureADTokenExchange')
} | ConvertTo-Json -Compress
$credentialFile = [System.IO.Path]::GetTempFileName()
try {
  $credential | Set-Content -LiteralPath $credentialFile -Encoding utf8NoBOM
  $null = & az ad app federated-credential create --only-show-errors --id $clientId --parameters "@$credentialFile" --output none
  if ($LASTEXITCODE -ne 0) { throw "OIDC federated credential creation failed." }
} finally {
  Remove-Item -LiteralPath $credentialFile -Force -ErrorAction SilentlyContinue
}
$null = & az role assignment create --only-show-errors --assignee-object-id $spObjectId --assignee-principal-type ServicePrincipal --role Contributor --scope $resourceGroupId --output none
if ($LASTEXITCODE -ne 0) { throw "Resource Group scoped Contributor assignment failed." }
$tenantId = & az account show --query tenantId --output tsv
Write-Output "GitHub OIDC created. Set environment variables without secrets:"
Write-Output "AZURE_CLIENT_ID=$clientId"
Write-Output "AZURE_TENANT_ID=$tenantId"
Write-Output "AZURE_SUBSCRIPTION_ID=$SubscriptionId"
Write-Output "No client secret was created. Live workflow login is still unverified."
