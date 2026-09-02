param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('code-review', 'test')]
    [string]$Gate,

    [Parameter(Mandatory = $true)]
    [ValidateSet('PASS', 'PENDING')]
    [string]$Status,

    [string]$Report = '',
    [string]$CommandResultsJson = '[]'
)

. (Join-Path $PSScriptRoot 'hook-utils.ps1')

$root = Get-ProjectRoot ([pscustomobject]@{ cwd = (Get-Location).Path })
$snapshot = Get-ProjectSnapshot -ProjectRoot $root
$commandResults = @()
try {
    $parsed = $CommandResultsJson | ConvertFrom-Json
    if ($null -ne $parsed) {
        $commandResults = @($parsed)
    }
}
catch {
    throw 'CommandResultsJson must be a JSON array of command result objects.'
}

$reportRelative = $null
$reportSha256 = $null
if (-not [string]::IsNullOrWhiteSpace($Report)) {
    $reportFull = [System.IO.Path]::GetFullPath((Join-Path $root $Report))
    if (-not $reportFull.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Report path must stay inside the project root.'
    }
    if (-not (Test-Path -LiteralPath $reportFull -PathType Leaf)) {
        throw "QA report does not exist: $Report"
    }
    $reportRelative = $reportFull.Substring($root.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
    $reportSha256 = Get-FileSha256 -Path $reportFull
}

if ($Status -eq 'PASS') {
    if ([string]::IsNullOrWhiteSpace($reportRelative)) {
        throw 'PASS requires a report file.'
    }
    if ($commandResults.Count -eq 0) {
        throw 'PASS requires at least one structured command result.'
    }
    foreach ($result in $commandResults) {
        if ($null -eq $result -or [string]::IsNullOrWhiteSpace([string]$result.command) -or $null -eq $result.exit_code -or [int]$result.exit_code -ne 0 -or [string]::IsNullOrWhiteSpace([string]$result.result_summary)) {
            throw 'Each PASS command result requires command, exit_code=0, and result_summary.'
        }
    }
}

$artifactPath = Join-Path $root ".codebuddy/$Gate-pass.flag"
$role = if ($Gate -eq 'code-review') { 'code_reviewer' } else { 'tester' }
Write-AtomicUtf8Json -Path $artifactPath -Value ([ordered]@{
    schema_version = 1
    status = $Status
    gate = $Gate
    agent_role = $role
    head_sha = $snapshot.head_sha
    tree_sha = $snapshot.tree_sha
    snapshot_hash = $snapshot.snapshot_hash
    files = @(Get-ChangedProjectFiles -ProjectRoot $root)
    commands = $commandResults
    report = $reportRelative
    report_sha256 = $reportSha256
    reason = if ($Status -eq 'PASS') { $null } else { 'QA validation started or did not pass.' }
    timestamp_utc = [DateTime]::UtcNow.ToString('o')
})

Write-Output "$Gate gate is now $Status for snapshot $($snapshot.snapshot_hash)."
