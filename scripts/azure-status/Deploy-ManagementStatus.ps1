[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $SubscriptionId,
    [Parameter(Mandatory)] [string] $TenantId,
    [Parameter(Mandatory)] [string] $OperatorObjectId,
    [Parameter(Mandatory)] [string] $Image,
    [Parameter(Mandatory)] [ValidateSet('JPY')] [string] $ConfirmedBillingCurrency,
    [Parameter(Mandatory)] [string] $BudgetContactEmail,
    [Parameter(Mandatory)] [string] $ApprovedHeadSha,
    [Parameter(Mandatory)] [string] $CostConfirmationSource,
    [Parameter(Mandatory)] [datetimeoffset] $CostConfirmedAt,
    [Parameter(Mandatory)] [datetimeoffset] $BudgetContactConfirmedAt,
    [Parameter(Mandatory)] [switch] $OperatorConfirmedBudgetContact,
    [Parameter(Mandatory)] [ValidateSet('Core', 'Protected')] [string] $Phase,
    [string] $IngestObjectId = '',
    [string] $AuthClientId = '',
    [string] $ResourceGroupName = 'rg-agent-world-mgmt-jpe',
    [string] $Location = 'japaneast',
    [string] $AppName = 'agent-world-status-yomote-jpe',
    [int] $BudgetAmount = 1000,
    [Parameter(Mandatory)] [string] $BudgetStartDate,
    [string] $PlanOutput = 'artifacts/azure-status/what-if.json',
    [switch] $Apply,
    [switch] $ApproveReviewedPlan,
    [string] $ApprovedPlanSha256 = '',
    [string] $ApprovedConfirmationSha256 = ''
)

$ErrorActionPreference = 'Stop'
$guidPattern = '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
foreach ($value in @($SubscriptionId, $TenantId, $OperatorObjectId)) {
    if ($value -notmatch $guidPattern) { throw 'Subscription, tenant, and operator IDs must be GUIDs.' }
}
$SubscriptionId = ([guid] $SubscriptionId).ToString('D')
$TenantId = ([guid] $TenantId).ToString('D')
$OperatorObjectId = ([guid] $OperatorObjectId).ToString('D')
if ($Image -notmatch '@sha256:[0-9a-f]{64}$') {
    throw 'Image must be an immutable sha256 digest reference.'
}
if ($ApprovedHeadSha -notmatch '^[0-9a-f]{40}$') {
    throw 'ApprovedHeadSha must be the reviewed 40-character commit SHA.'
}
if ([string]::IsNullOrWhiteSpace($CostConfirmationSource)) {
    throw 'CostConfirmationSource must identify the read-only billing evidence reviewed by the operator.'
}
if (-not $OperatorConfirmedBudgetContact) {
    throw 'OperatorConfirmedBudgetContact is required.'
}
if ($BudgetContactEmail -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
    throw 'BudgetContactEmail must be the confirmed notification address.'
}
if ($BudgetStartDate -notmatch '^\d{4}-\d{2}-01T00:00:00Z$') {
    throw 'BudgetStartDate must be the first day of the month at 00:00:00Z.'
}
if ($Phase -eq 'Protected' -and ([string]::IsNullOrWhiteSpace($IngestObjectId) -or [string]::IsNullOrWhiteSpace($AuthClientId))) {
    throw 'Protected phase requires the approved ingest object ID and auth client ID.'
}
if ($Phase -eq 'Protected' -and ($IngestObjectId -notmatch $guidPattern -or $AuthClientId -notmatch $guidPattern)) {
    throw 'Protected phase identity IDs must be GUIDs.'
}
if ($Phase -eq 'Protected') {
    $IngestObjectId = ([guid] $IngestObjectId).ToString('D')
    $AuthClientId = ([guid] $AuthClientId).ToString('D')
}
if ($Apply -and -not $ApproveReviewedPlan) {
    throw 'Apply requires -ApproveReviewedPlan after the saved what-if has been reviewed.'
}
if ($Apply -and $ApprovedPlanSha256 -notmatch '^[0-9a-f]{64}$') {
    throw 'Apply requires the SHA-256 of the reviewed what-if JSON.'
}
if ($Apply -and $ApprovedConfirmationSha256 -notmatch '^[0-9a-f]{64}$') {
    throw 'Apply requires the SHA-256 of the reviewed confirmation record.'
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$currentHead = (& git -C $repositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $currentHead -ne $ApprovedHeadSha) {
    throw 'Current repository head does not match ApprovedHeadSha.'
}

$azCommand = Get-Command az -ErrorAction SilentlyContinue
$azPath = if ($azCommand) { $azCommand.Source } else { 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd' }
if (-not (Test-Path -LiteralPath $azPath)) { throw 'Azure CLI was not found.' }

$account = & $azPath account show --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $account.id -ne $SubscriptionId -or $account.tenantId -ne $TenantId) {
    throw 'Azure context does not match the approved subscription and tenant.'
}
if ($account.user.type -ne 'user') {
    throw 'Azure CLI must be signed in as the approved human deployment operator.'
}
$signedInObjectId = (& $azPath ad signed-in-user show --query id --output tsv).Trim()
if ($LASTEXITCODE -ne 0 -or $signedInObjectId -notmatch $guidPattern -or $signedInObjectId -ne $OperatorObjectId) {
    throw 'Signed-in Azure user object ID does not match OperatorObjectId.'
}

$protected = $Phase -eq 'Protected'
$templateName = if ($protected) { 'protected-main.bicep' } else { 'main.bicep' }
$template = Join-Path $PSScriptRoot "..\..\infra\azure-status\$templateName"
$parameters = @(
    "resourceGroupName=$ResourceGroupName"
    "location=$Location"
    "appName=$AppName"
    "image=$Image"
    "tenantId=$TenantId"
    "authClientId=$AuthClientId"
    "operatorObjectId=$OperatorObjectId"
    "ingestObjectId=$IngestObjectId"
)
if (-not $protected) {
    $parameters += @(
        "confirmedBillingCurrency=$ConfirmedBillingCurrency"
        "budgetContactEmail=$BudgetContactEmail"
        "budgetAmount=$BudgetAmount"
        "budgetStartDate=$BudgetStartDate"
        'enableProtectedIngress=false'
    )
}

$confirmationRecord = [ordered]@{
    headSha = $ApprovedHeadSha
    subscriptionId = $SubscriptionId
    tenantId = $TenantId
    operatorObjectId = $OperatorObjectId
    phase = $Phase
    resourceGroupName = $ResourceGroupName
    location = $Location
    appName = $AppName
    image = $Image
    billingCurrency = $ConfirmedBillingCurrency
    costConfirmationSource = $CostConfirmationSource
    costConfirmedAt = $CostConfirmedAt.ToUniversalTime().ToString('o')
    budgetContactEmail = $BudgetContactEmail
    budgetContactConfirmedAt = $BudgetContactConfirmedAt.ToUniversalTime().ToString('o')
    budgetAmount = $BudgetAmount
    budgetStartDate = $BudgetStartDate
    authClientId = $AuthClientId
    ingestObjectId = $IngestObjectId
}
$confirmationJson = $confirmationRecord | ConvertTo-Json -Compress
$confirmationBytes = [Text.Encoding]::UTF8.GetBytes($confirmationJson)
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $confirmationHashBytes = $sha256.ComputeHash($confirmationBytes)
}
finally {
    $sha256.Dispose()
}
$confirmationHash = ([BitConverter]::ToString($confirmationHashBytes) -replace '-', '').ToLowerInvariant()

$json = & $azPath deployment sub what-if `
    --name "agent-world-management-status-$($Phase.ToLowerInvariant())" `
    --location $Location `
    --template-file $template `
    --parameters $parameters `
    --result-format FullResourcePayloads `
    --no-pretty-print `
    --output json
if ($LASTEXITCODE -ne 0) {
    throw 'Azure what-if failed. Apply was not attempted.'
}
$plan = $json | ConvertFrom-Json
$changes = @($plan.changes)
if ($changes.Count -eq 0) {
    throw 'Azure what-if returned no resource changes; the result cannot be accepted.'
}

$subscriptionRoot = "/subscriptions/$SubscriptionId"
$resourceGroupId = "$subscriptionRoot/resourceGroups/$ResourceGroupName"
$escapedResourceGroupId = [regex]::Escape($resourceGroupId)
$escapedAppName = [regex]::Escape($AppName)
$guidId = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
$storageId = "$escapedResourceGroupId/providers/Microsoft\.Storage/storageAccounts/awst[a-z0-9]{13}"
$keyVaultId = "$escapedResourceGroupId/providers/Microsoft\.KeyVault/vaults/awst[a-z0-9]{13}"
$coreResourcePatterns = @(
    "^$escapedResourceGroupId$",
    "^$escapedResourceGroupId/providers/Microsoft\.ManagedIdentity/userAssignedIdentities/$escapedAppName-identity$",
    "^$storageId$",
    "^$storageId/blobServices/default$",
    "^$storageId/blobServices/default/containers/status$",
    "^$storageId/blobServices/default/containers/status/providers/Microsoft\.Authorization/roleAssignments/$guidId$",
    "^$keyVaultId$",
    "^$keyVaultId/providers/Microsoft\.Authorization/roleAssignments/$guidId$",
    "^$escapedResourceGroupId/providers/Microsoft\.OperationalInsights/workspaces/$escapedAppName-logs$",
    "^$escapedResourceGroupId/providers/Microsoft\.App/managedEnvironments/$escapedAppName-env$",
    "^$escapedResourceGroupId/providers/Microsoft\.App/containerApps/$escapedAppName$",
    "^$escapedResourceGroupId/providers/Microsoft\.Consumption/budgets/$escapedAppName-monthly-jpy$"
)
$appId = "$resourceGroupId/providers/Microsoft.App/containerApps/$AppName"
$authId = "$appId/authConfigs/current"
$unexpected = @()
foreach ($change in $changes) {
    $id = [string] $change.resourceId
    $isCoreResource = @($coreResourcePatterns | Where-Object { $id -match $_ }).Count -gt 0
    $isAuthResource = $id -ieq $authId
    if (-not $isCoreResource -and -not ($Phase -eq 'Protected' -and $isAuthResource)) {
        $unexpected += "OutOfScope $($change.changeType) $id"
        continue
    }
    if ($change.changeType -eq 'NoChange') { continue }
    if ($Phase -eq 'Core' -and $change.changeType -eq 'Create') { continue }
    if ($Phase -eq 'Protected' -and $change.changeType -eq 'Create' -and $isAuthResource) { continue }
    if ($Phase -eq 'Protected' -and $change.changeType -eq 'Modify' -and $id -ieq $appId) { continue }
    $unexpected += "$($change.changeType) $id"
}
if ($unexpected.Count -gt 0) {
    throw "What-if contains changes outside the $Phase allowlist:`n$($unexpected -join "`n")"
}
if ($Phase -eq 'Core') {
    $resourceGroupChange = @($changes | Where-Object {
        $_.resourceId -match "/resourceGroups/$([regex]::Escape($ResourceGroupName))$"
    })
    if ($resourceGroupChange.Count -ne 1 -or $resourceGroupChange[0].changeType -ne 'Create') {
        throw 'Core requires the dedicated Resource Group itself to be a single Create.'
    }
}

$planPath = [IO.Path]::GetFullPath((Join-Path $PWD $PlanOutput))
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($planPath)) | Out-Null
[IO.File]::WriteAllText($planPath, $json, [Text.UTF8Encoding]::new($false))
$planHash = (Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Reviewed-plan candidate saved: $planPath"
Write-Host "Plan SHA-256: $planHash"
Write-Host "Confirmation SHA-256: $confirmationHash"

if (-not $Apply) {
    Write-Host 'What-if only. No resource was created or modified.'
    exit 0
}
if ($planHash -ne $ApprovedPlanSha256.ToLowerInvariant()) {
    throw 'The current what-if differs from the plan approved by the user.'
}
if ($confirmationHash -ne $ApprovedConfirmationSha256.ToLowerInvariant()) {
    throw 'The deployment parameters or confirmation evidence differ from the approved record.'
}

& $azPath deployment sub create `
    --name "agent-world-management-status-$($Phase.ToLowerInvariant())" `
    --location $Location `
    --template-file $template `
    --parameters $parameters `
    --output json | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Azure deployment result was not successful. Do not retry before inspecting actual state.'
}
Write-Host "Azure $Phase deployment completed once."
