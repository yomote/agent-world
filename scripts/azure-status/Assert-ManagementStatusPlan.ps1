function Assert-ManagementStatusPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object[]] $Changes,
        [Parameter(Mandatory)] [ValidateSet('Core', 'Protected')] [string] $Phase,
        [Parameter(Mandatory)] [string] $SubscriptionId,
        [Parameter(Mandatory)] [string] $ResourceGroupName,
        [Parameter(Mandatory)] [string] $AppName
    )

    if ($Changes.Count -eq 0) {
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
    foreach ($change in $Changes) {
        $id = [string] $change.resourceId
        $isCoreResource = @($coreResourcePatterns | Where-Object { $id -match $_ }).Count -gt 0
        $isAuthResource = $id -ieq $authId
        if (-not $isCoreResource -and -not ($Phase -eq 'Protected' -and $isAuthResource)) {
            $unexpected += "OutOfScope $($change.changeType) $id"
            continue
        }
        # Existing dependencies are non-write Ignore entries. Scope is checked first.
        if ($change.changeType -in @('NoChange', 'Ignore')) { continue }
        if ($Phase -eq 'Core' -and $change.changeType -eq 'Create') { continue }
        if ($Phase -eq 'Protected' -and $change.changeType -eq 'Create' -and $isAuthResource) { continue }
        if ($Phase -eq 'Protected' -and $change.changeType -eq 'Modify' -and $id -ieq $appId) { continue }
        $unexpected += "$($change.changeType) $id"
    }
    if ($unexpected.Count -gt 0) {
        throw "What-if contains changes outside the $Phase allowlist:`n$($unexpected -join "`n")"
    }
    if ($Phase -eq 'Core') {
        $resourceGroupChange = @($Changes | Where-Object {
            $_.resourceId -match "/resourceGroups/$([regex]::Escape($ResourceGroupName))$"
        })
        if ($resourceGroupChange.Count -ne 1 -or $resourceGroupChange[0].changeType -ne 'Create') {
            throw 'Core requires the dedicated Resource Group itself to be a single Create.'
        }
    }
}
