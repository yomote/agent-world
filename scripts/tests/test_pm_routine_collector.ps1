$ErrorActionPreference = 'Stop'
$report = Join-Path $env:TEMP 'pm-routine-collector-test.md'
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, $Headers, [int]$TimeoutSec, [string]$Uri)
    if ($Uri -match '/issues\?') { return [pscustomobject]@{ Content = '[{"number":1,"state":"open","title":"issue","assignees":[],"labels":[],"body":"#2"},{"number":2,"state":"open","title":"pr-as-issue","pull_request":{"url":"x"},"assignees":[],"labels":[],"body":""}]' } }
    if ($Uri -match '/pulls\?') { return [pscustomobject]@{ Content = '[{"number":2,"state":"open","title":"pr","merged_at":null}]' } }
    return [pscustomobject]@{ Content = '{"number":53,"state":"open","title":"focus","assignees":[],"labels":[],"body":"next #2"}' }
}
& "$PSScriptRoot\..\automation\collect_pm_routine.ps1" -ReportPath $report -ApiBase 'https://mock.local/repo' -NoExit
$content = Get-Content -Raw $report
if ($content -notmatch 'state: completed' -or $content -notmatch 'issues_observed: 1' -or $content -notmatch 'prs_observed: 1') { throw 'collector did not classify issue and PR inventory' }
Remove-Item -LiteralPath $report -Force
