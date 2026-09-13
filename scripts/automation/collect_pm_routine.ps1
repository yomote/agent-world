[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ReportPath, [string]$ApiBase = 'https://api.github.com/repos/yomote/agent-world', [switch]$NoExit)

$ErrorActionPreference = 'Stop'
$base = $ApiBase.TrimEnd('/')
$headers = @{ 'User-Agent' = 'agent-world-pm-routine'; 'Accept' = 'application/vnd.github+json' }
$errors = @(); $reads = 0; $stopped = $false
function Read-Api([string]$Path) {
    if ($script:stopped) { return $null }
    $script:reads += 1
    try { return ((Invoke-WebRequest -UseBasicParsing -Headers $headers -TimeoutSec 15 -Uri "$base$Path").Content | ConvertFrom-Json) }
    catch { $script:errors += $_.Exception.Message; $script:stopped = $true; return $null }
}
$issues = @(Read-Api '/issues?state=all&per_page=100') | Where-Object { $null -eq $_.pull_request.url }
$prs = Read-Api '/pulls?state=all&per_page=100'
$focus = @(); foreach ($n in 53,66,79,80,82,83) { $v=Read-Api "/issues/$n"; if ($null -ne $v) { $focus += $v } }
$issueItems = @($(if ($null -eq $issues) { @() } else { @($issues) })); $prItems = @($(if ($null -eq $prs) { @() } else { @($prs) }))
$successes = $issueItems.Count + $prItems.Count + $focus.Count
$inventoryMissing = $issueItems.Count -eq 0 -and $focus.Count -gt 0
$state = if ($errors.Count -eq 0 -and -not $inventoryMissing) { 'completed' } elseif ($successes -eq 0) { 'failed' } else { 'partial' }
$lines = @('# PM routine report', "captured_at: $((Get-Date).ToString('o'))", "state: $state", "issues_observed: $($issueItems.Count)", "prs_observed: $($prItems.Count)", 'completed:')
foreach ($i in $issueItems | Where-Object { $_.state -eq 'closed' }) { $lines += "- issue #$($i.number) closed: $($i.title)" }
$lines += 'running:'; foreach ($i in $issueItems | Where-Object { $_.state -eq 'open' }) { $lines += "- issue #$($i.number) open: $($i.title)" }
$lines += 'pr_status:'; foreach ($p in $prItems) { $kind=if($p.merged_at){'merged'}elseif($p.state -eq 'closed'){'closed_unmerged'}else{'open'}; $lines += "- pr #$($p.number) ${kind}: $($p.title)" }
$lines += 'focus:'; foreach ($i in $focus) { $labels=($i.labels|ForEach-Object{$_.name})-join ','; $owners=($i.assignees|ForEach-Object{$_.login})-join ','; if(-not $owners){$owners='unconfirmed'}; $body=[string]$i.body; $related=([regex]::Matches($body,'(?<!\w)#\d+')|ForEach-Object{$_.Value}|Sort-Object -Unique)-join ','; if(-not $related){$related='unconfirmed'}; $excerpt=($body -replace "[\r\n]+",' ').Trim(); if($excerpt.Length -gt 240){$excerpt=$excerpt.Substring(0,240)}; $lines += "- issue #$($i.number) $($i.state) owner=$owners labels=$labels related_pr_or_issue=$related next_action=unconfirmed dependency=unconfirmed evidence_excerpt=$excerpt" }
$lines += @('ci: unobserved', 'worker_runtime: unobserved', 'dashboard: unobserved; no dashboard read path is configured.', 'escape: #53 -> #66 -> #79 -> #80; dependency details are unconfirmed unless present in focus evidence.', 'next: PM reads this inventory with canonical Issue and PR records before dispatching one work unit.', "limits: GitHub REST reads=$reads maximum 8; no external post, label update, claim, or worker dispatch.")
if ($inventoryMissing) { $lines += 'issues_inventory: unconfirmed; focus details exist but the all-issues inventory was empty.' }
if ($errors.Count) { $lines += 'errors:'; $lines += ($errors | ForEach-Object { "- $_" }) }
$lines | Set-Content -LiteralPath $ReportPath -Encoding utf8
if (-not $NoExit -and ($errors.Count -or $inventoryMissing)) { exit 2 }
