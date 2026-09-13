[CmdletBinding()]
param(
    [switch]$Replace,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"
$taskPath = "\Codex\"
$taskFolderPath = "\Codex"
$taskName = "AgentWorldPmRoutine"
$launcher = Join-Path $PSScriptRoot "run_pm_routine.ps1"
$prompt = Join-Path $PSScriptRoot "pm_routine_prompt.md"
$commonDirectory = (& git -C (Resolve-Path (Join-Path $PSScriptRoot "..\..")) rev-parse --path-format=absolute --git-common-dir).Trim()
if ($LASTEXITCODE -ne 0 -or -not $commonDirectory) {
    throw "Unable to resolve the shared git common directory."
}
$runtimeDirectory = Join-Path $commonDirectory "codex\pm-routine"
$runtimeLauncher = Join-Path $runtimeDirectory "run_pm_routine.ps1"
$runtimePrompt = Join-Path $runtimeDirectory "pm_routine_prompt.md"
$collector = Join-Path $PSScriptRoot "collect_pm_routine.ps1"
$runtimeCollector = Join-Path $runtimeDirectory "collect_pm_routine.ps1"
$existing = Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue

if ($null -ne $existing -and -not $Replace) {
    throw "Existing task $taskPath$taskName was not changed. Inspect it before using -Replace."
}

# Create only the dedicated folder when it is absent.
$service = New-Object -ComObject "Schedule.Service"
$service.Connect()
try {
    $null = $service.GetFolder($taskFolderPath)
} catch {
    $null = $service.GetFolder("\").CreateFolder("Codex", $null)
}

New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null
Copy-Item -LiteralPath $launcher -Destination $runtimeLauncher -Force
Copy-Item -LiteralPath $prompt -Destination $runtimePrompt -Force
Copy-Item -LiteralPath $collector -Destination $runtimeCollector -Force

$arguments = "-NoProfile -File `"$runtimeLauncher`" -CommonDirectory `"$commonDirectory`""
$action = New-ScheduledTaskAction -Execute "$PSHOME\powershell.exe" -Argument $arguments `
    -WorkingDirectory $runtimeDirectory
$trigger = New-ScheduledTaskTrigger -Daily -At 09:00
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable:$false -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
$registered = Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName
if ($registered.State -eq "Disabled") {
    throw "The task is disabled after registration."
}

if ($RunNow) {
    Start-ScheduledTask -TaskPath $taskPath -TaskName $taskName
}

$registered | Select-Object TaskPath, TaskName, State, Actions, Triggers, Principal | Format-List
