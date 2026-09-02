. (Join-Path $PSScriptRoot 'hook-utils.ps1')

function Get-DemoQaFlagPath {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][ValidateSet('code-review', 'test')][string]$Gate
    )

    $name = if ($Gate -eq 'code-review') { 'code-review-pass.flag' } else { 'test-pass.flag' }
    return Join-Path $ProjectRoot ".codebuddy/$name"
}

function Get-DemoQaReportPath {
    return 'docs/04-quality/Demo' + [char]0x6D4B + [char]0x8BD5 + [char]0x8BB0 + [char]0x5F55 + '.md'
}

function Test-DemoSnapshotPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $path = $RelativePath.Replace('\', '/')
    return (
        $path -match '^(backend|frontend|tests)/' -or
        $path -match '^\.codex/' -or
        $path -match '^\.githooks/' -or
        $path -match '^\.codebuddy/hooks/' -or
        $path -eq '.workbuddy/settings.json' -or
        $path -match '^(AGENTS\.md|README\.md|\.gitignore|\.env\.example)$' -or
        $path -match '^[^/]+\.(py|bat|cmd|sh)$' -or
        $path -eq 'materials/.gitkeep'
    )
}

function Get-DemoProjectSnapshot {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $root = [System.IO.Path]::GetFullPath($ProjectRoot)
    $gitPaths = @(& git -C $root -c core.quotepath=false ls-files -co --exclude-standard 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw 'Cannot enumerate files for the Demo QA snapshot.'
    }

    $startupScript = [string]::Concat(
        [char]0x542F,
        [char]0x52A8,
        [char]0x52A9,
        [char]0x5B66,
        [char]0x52A9,
        [char]0x624B,
        '.bat'
    )
    $pathSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($gitPath in $gitPaths) {
        $relativePath = ([string]$gitPath).Trim()
        if ($relativePath -and $relativePath -notmatch '[^\x00-\x7F]') {
            $pathSet.Add($relativePath) | Out-Null
        }
    }
    $pathSet.Add($startupScript) | Out-Null
    $candidatePaths = [string[]]@($pathSet)
    [Array]::Sort($candidatePaths, [System.StringComparer]::Ordinal)

    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($relative in $candidatePaths) {
        $normalized = $relative.Replace('\', '/')
        if (-not (Test-DemoSnapshotPath -RelativePath $normalized)) {
            continue
        }

        $fullPath = Join-Path $root $relative
        if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
            $lines.Add("$normalized`t$(Get-FileSha256 -Path $fullPath)")
        }
        else {
            $lines.Add("$normalized`t<deleted>")
        }
    }

    $payload = $lines -join "`n"
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha.ComputeHash([System.Text.UTF8Encoding]::new($false).GetBytes($payload))
        $hash = ([System.BitConverter]::ToString($hashBytes)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }

    return [pscustomobject]@{
        snapshot_hash = $hash
        files = @($lines | ForEach-Object { ($_ -split "`t", 2)[0] })
    }
}

function Write-DemoQaArtifact {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][ValidateSet('code-review', 'test')][string]$Gate,
        [Parameter(Mandatory = $true)][ValidateSet('PASS', 'PENDING')][string]$Status,
        [Parameter(Mandatory = $true)][string]$Summary,
        [string]$Report = ''
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot)
    $snapshot = Get-DemoProjectSnapshot -ProjectRoot $root
    $flagPath = Get-DemoQaFlagPath -ProjectRoot $root -Gate $Gate
    $flagDir = Split-Path -Parent $flagPath
    if (-not (Test-Path -LiteralPath $flagDir)) {
        New-Item -ItemType Directory -Path $flagDir -Force | Out-Null
    }

    if ([string]::IsNullOrWhiteSpace($Report)) {
        $Report = Get-DemoQaReportPath
    }

    $artifact = [ordered]@{
        status = $Status
        snapshot_hash = $snapshot.snapshot_hash
        checked_at = [DateTime]::UtcNow.ToString('o')
        summary = $Summary
        report = $Report.Replace('\', '/')
    }
    $json = $artifact | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($flagPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    return [pscustomobject]$artifact
}

function Set-DemoQaGatePending {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][ValidateSet('code-review', 'test')][string]$Gate,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    Write-DemoQaArtifact -ProjectRoot $ProjectRoot -Gate $Gate -Status PENDING -Summary $Reason | Out-Null
}

function Get-DemoQaGateFailures {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $root = [System.IO.Path]::GetFullPath($ProjectRoot)
    $snapshot = Get-DemoProjectSnapshot -ProjectRoot $root
    $failures = [System.Collections.Generic.List[string]]::new()

    foreach ($gate in @('test', 'code-review')) {
        $label = if ($gate -eq 'test') { 'tester' } else { 'code_reviewer' }
        $flagPath = Get-DemoQaFlagPath -ProjectRoot $root -Gate $gate
        if (-not (Test-Path -LiteralPath $flagPath -PathType Leaf)) {
            $failures.Add("$label Demo gate is missing.")
            continue
        }

        try {
            $artifact = Get-Content -LiteralPath $flagPath -Raw | ConvertFrom-Json
        }
        catch {
            $failures.Add("$label Demo gate is not valid JSON.")
            continue
        }

        if ([string]$artifact.status -ne 'PASS') {
            $reason = if ([string]::IsNullOrWhiteSpace([string]$artifact.summary)) { 'not passed' } else { [string]$artifact.summary }
            $failures.Add("$label status is not PASS: $reason")
        }
        if ([string]::IsNullOrWhiteSpace([string]$artifact.snapshot_hash) -or [string]$artifact.snapshot_hash -ne $snapshot.snapshot_hash) {
            $failures.Add("$label result is stale because the business/test snapshot changed.")
        }
        if ([string]::IsNullOrWhiteSpace([string]$artifact.checked_at) -or [string]::IsNullOrWhiteSpace([string]$artifact.summary)) {
            $failures.Add("$label Demo gate is missing checked_at or summary.")
        }
        if ([string]::IsNullOrWhiteSpace([string]$artifact.report)) {
            $failures.Add("$label Demo gate does not reference the shared test record.")
        }
    }

    return $failures
}
