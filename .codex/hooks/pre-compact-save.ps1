. (Join-Path $PSScriptRoot 'hook-utils.ps1')

$hookInput = Read-HookInput
$root = Get-ProjectRoot $hookInput
$trigger = 'unknown'
if ($hookInput.PSObject.Properties.Name -contains 'trigger') {
    $trigger = [string]$hookInput.trigger
}

$memoryDir = Join-Path $root '.workbuddy/memory'
New-Item -ItemType Directory -Path $memoryDir -Force | Out-Null
$snapshotPath = Join-Path $memoryDir 'compact-snapshot.md'

$latestDailyName = 'none'
$latestDailyTail = 'none'
$latestDaily = Get-ChildItem -LiteralPath $memoryDir -File -Filter '????-??-??.md' |
    Sort-Object Name -Descending |
    Select-Object -First 1
if ($null -ne $latestDaily) {
    $latestDailyName = $latestDaily.Name
    $latestDailyTail = ((Get-Content -LiteralPath $latestDaily.FullName -Tail 120 -Encoding UTF8) -join "`n").Trim()
}

$scratchList = 'none'
$scratchDir = Join-Path $root '_scratch'
if (Test-Path -LiteralPath $scratchDir -PathType Container) {
    $names = Get-ChildItem -LiteralPath $scratchDir -File -Filter '*.md' | Select-Object -ExpandProperty Name
    if ($names) {
        $scratchList = $names -join "`n"
    }
}

$gitStatus = 'unavailable'
$recentCommits = 'unavailable'
$candidateSnapshot = 'unavailable'
try {
    $gitStatus = ((& git -C $root status --short --branch 2>&1) -join "`n").Trim()
    $recentCommits = ((& git -C $root log -5 --oneline --decorate 2>&1) -join "`n").Trim()
    $snapshot = Get-ProjectSnapshot -ProjectRoot $root
    $candidateSnapshot = "HEAD=$($snapshot.head_sha)`nTREE=$($snapshot.tree_sha)`nSNAPSHOT=$($snapshot.snapshot_hash)"
}
catch {
    # Keep fallback text in the snapshot.
}

$qaState = [System.Collections.Generic.List[string]]::new()
foreach ($gate in @('code-review', 'test')) {
    $gatePath = Join-Path $root ".codebuddy/$gate-pass.flag"
    if (-not (Test-Path -LiteralPath $gatePath -PathType Leaf)) {
        $qaState.Add("${gate}: missing")
        continue
    }
    try {
        $artifact = Get-Content -LiteralPath $gatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $qaState.Add("${gate}: status=$($artifact.status), snapshot=$($artifact.snapshot_hash), report=$($artifact.report)")
    }
    catch {
        $qaState.Add("${gate}: invalid legacy or malformed artifact")
    }
}

$content = @"
# Codex pre-compaction project snapshot

> Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
> Trigger: $trigger
> Purpose: restored by the SessionStart hook after compaction

## Current phase

Phase 0 core functionality is implemented. Current work focuses on bug fixes and real local integration.

## Git status

~~~text
$gitStatus
~~~

## Recent commits

~~~text
$recentCommits
~~~

## Candidate snapshot

~~~text
$candidateSnapshot
~~~

## QA evidence

$($qaState -join "`n")

## Latest work-log tail ($latestDailyName)

$latestDailyTail

## _scratch artifacts

$scratchList
"@

[System.IO.File]::WriteAllText($snapshotPath, $content.Trim() + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
exit 0
