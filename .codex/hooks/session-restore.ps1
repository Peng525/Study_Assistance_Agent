. (Join-Path $PSScriptRoot 'hook-utils.ps1')

$hookInput = Read-HookInput
$root = Get-ProjectRoot $hookInput
$memoryDir = Join-Path $root '.workbuddy/memory'
$sections = [System.Collections.Generic.List[string]]::new()

$rules = Join-Path $root 'AGENTS.md'
if (Test-Path -LiteralPath $rules -PathType Leaf) {
    $sections.Add("=== Current project rules (authoritative) ===`n" + (Get-Content -LiteralPath $rules -Raw -Encoding UTF8).Trim())
}

try {
    $head = ((& git -C $root rev-parse HEAD 2>&1) -join "`n").Trim()
    $status = ((& git -C $root status --short --branch 2>&1) -join "`n").Trim()
    $sections.Add("=== Current repository state ===`nHEAD: $head`n$status")
}
catch {
    $sections.Add('=== Current repository state ===`nUnavailable; inspect git status before acting.')
}

$qaLines = [System.Collections.Generic.List[string]]::new()
foreach ($gate in @('code-review', 'test')) {
    $gatePath = Join-Path $root ".codebuddy/$gate-pass.flag"
    if (Test-Path -LiteralPath $gatePath -PathType Leaf) {
        try {
            $artifact = Get-Content -LiteralPath $gatePath -Raw -Encoding UTF8 | ConvertFrom-Json
            $qaLines.Add("${gate}: status=$($artifact.status), head=$($artifact.head_sha), snapshot=$($artifact.snapshot_hash), time=$($artifact.timestamp_utc)")
        }
        catch {
            $qaLines.Add("${gate}: invalid legacy or malformed artifact; treat as PENDING")
        }
    }
    else {
        $qaLines.Add("${gate}: missing; treat as PENDING")
    }
}
$sections.Add("=== Current QA state ===`n" + ($qaLines -join "`n"))

$longTerm = Join-Path $memoryDir 'MEMORY.md'
if (Test-Path -LiteralPath $longTerm -PathType Leaf) {
    $sections.Add("=== Long-term project memory ===`n" + (Get-Content -LiteralPath $longTerm -Raw -Encoding UTF8).Trim())
}

if (Test-Path -LiteralPath $memoryDir -PathType Container) {
    $latestDaily = Get-ChildItem -LiteralPath $memoryDir -File -Filter '????-??-??.md' |
        Sort-Object Name -Descending |
        Select-Object -First 1
    if ($null -ne $latestDaily) {
        $sections.Add("=== Latest work log: $($latestDaily.Name) ===`n" + (Get-Content -LiteralPath $latestDaily.FullName -Raw -Encoding UTF8).Trim())
    }
}

$snapshot = Join-Path $memoryDir 'compact-snapshot.md'
if (Test-Path -LiteralPath $snapshot -PathType Leaf) {
    $sections.Add("=== Previous pre-compaction snapshot ===`n" + (Get-Content -LiteralPath $snapshot -Raw -Encoding UTF8).Trim())
}

$scratch = Join-Path $root '_scratch'
if (Test-Path -LiteralPath $scratch -PathType Container) {
    $scratchFiles = Get-ChildItem -LiteralPath $scratch -File -Filter '*.md' | Select-Object -ExpandProperty Name
    if ($scratchFiles) {
        $sections.Add("=== _scratch artifacts ===`n" + ($scratchFiles -join "`n"))
    }
}

$changelog = Join-Path $root 'CHANGELOG.md'
if (Test-Path -LiteralPath $changelog -PathType Leaf) {
    $head = Get-Content -LiteralPath $changelog -TotalCount 30 -Encoding UTF8
    $sections.Add("=== Recent CHANGELOG ===`n" + ($head -join "`n"))
}

if ($sections.Count -gt 0) {
    $context = "Project context restored. The current user instruction takes precedence over restored notes.`n`n" + ($sections -join "`n`n")
    Write-HookJson (New-AdditionalContextOutput -EventName 'SessionStart' -Context $context)
}

exit 0
