[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CommonDirectory
)

$ErrorActionPreference = "Stop"
$CommonDirectory = (Resolve-Path $CommonDirectory).Path
$outputDirectory = Join-Path $CommonDirectory "codex\pm-routine\reports"
$collectorPath = Join-Path $PSScriptRoot "collect_pm_routine.ps1"
$latestReport = Join-Path $outputDirectory "latest.md"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runReport = Join-Path $outputDirectory "run-$timestamp.md"
$progressLog = Join-Path $outputDirectory "run-$timestamp.log"

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
@(
    "# PM routine"
    "started_at: $((Get-Date).ToUniversalTime().ToString('o'))"
    "state: running"
    "progress_log: $progressLog"
    "limits: The run has started. Completion, external observations, and report contents are not yet verified."
) | Set-Content -LiteralPath $latestReport -Encoding utf8

try {
    & $collectorPath -ReportPath $runReport `
        2>&1 | Tee-Object -LiteralPath $progressLog
    $exitCode = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath $runReport)) {
        throw "collector failed (exit=$exitCode)"
    }
    Copy-Item -LiteralPath $runReport -Destination $latestReport -Force
    exit $exitCode
} catch {
    # Preserve a visible failure report for the next Front Desk read.
    @(
        "# PM routine"
        "取得時刻: $((Get-Date).ToUniversalTime().ToString('o'))"
        "state: not_run"
        "reason: $($_.Exception.Message)"
        "limits: Collector launch or report save failed; no external post, claim, or worker dispatch occurred. See $progressLog for observed progress."
    ) | Set-Content -LiteralPath $latestReport -Encoding utf8
    exit 1
}
