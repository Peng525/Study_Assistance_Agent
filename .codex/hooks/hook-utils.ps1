Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Read-HookInput {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return [pscustomobject]@{}
    }
    return $raw | ConvertFrom-Json
}

function Get-ProjectRoot {
    param([object]$HookInput)

    $start = $null
    if ($null -ne $HookInput -and $HookInput.PSObject.Properties.Name -contains 'cwd') {
        $start = [string]$HookInput.cwd
    }
    if ([string]::IsNullOrWhiteSpace($start)) {
        $start = (Get-Location).Path
    }

    try {
        $gitRoot = (& git -C $start rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -eq 0 -and $gitRoot) {
            return [System.IO.Path]::GetFullPath(([string]$gitRoot).Trim())
        }
    }
    catch {
        # Fall through to directory traversal.
    }

    $current = [System.IO.DirectoryInfo]::new([System.IO.Path]::GetFullPath($start))
    while ($null -ne $current) {
        if (Test-Path -LiteralPath (Join-Path $current.FullName '.git')) {
            return $current.FullName
        }
        $current = $current.Parent
    }
    throw "Cannot locate project root from: $start"
}

function Write-HookJson {
    param([Parameter(Mandatory = $true)][object]$Value)
    $Value | ConvertTo-Json -Depth 12 -Compress | Write-Output
}

function New-AdditionalContextOutput {
    param(
        [Parameter(Mandatory = $true)][string]$EventName,
        [Parameter(Mandatory = $true)][string]$Context
    )
    return @{
        hookSpecificOutput = @{
            hookEventName = $EventName
            additionalContext = $Context
        }
    }
}

function Get-HookCommand {
    param([object]$HookInput)

    if ($null -eq $HookInput -or -not ($HookInput.PSObject.Properties.Name -contains 'tool_input') -or $null -eq $HookInput.tool_input) {
        return ''
    }
    foreach ($name in @('command', 'cmd')) {
        if ($HookInput.tool_input.PSObject.Properties.Name -contains $name) {
            return [string]$HookInput.tool_input.$name
        }
    }
    return ''
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fileSha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        return ([System.BitConverter]::ToString($fileSha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $stream.Dispose()
        $fileSha.Dispose()
    }
}

function Get-ProjectSnapshot {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $root = [System.IO.Path]::GetFullPath($ProjectRoot)
    $head = ((& git -C $root rev-parse HEAD 2>$null) -join "`n").Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($head)) {
        throw 'Cannot resolve HEAD for QA snapshot.'
    }

    $paths = @(& git -C $root -c core.quotepath=false ls-files --cached --others --exclude-standard 2>$null | Sort-Object -Unique)
    if ($LASTEXITCODE -ne 0) {
        throw 'Cannot enumerate files for QA snapshot.'
    }
    $manifest = [System.Text.StringBuilder]::new()
    foreach ($relativePath in $paths) {
        $normalized = ([string]$relativePath).Replace('\', '/')
        if ([string]::IsNullOrWhiteSpace($normalized)) {
            continue
        }
        $isSensitive = $normalized -match '(?i)(?:^|/)\.env(?:\.|$)' -or $normalized -match '(?i)\.(?:db|sqlite|sqlite3)$'
        $fullPath = [System.IO.Path]::GetFullPath((Join-Path $root $normalized))
        if (-not $fullPath.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Snapshot path escapes project root: $normalized"
        }
        if ($isSensitive) {
            [void]$manifest.AppendLine("$normalized`tSENSITIVE_EXCLUDED")
            continue
        }
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            [void]$manifest.AppendLine("$normalized`tMISSING")
            continue
        }
        $fileHash = Get-FileSha256 -Path $fullPath
        $size = (Get-Item -LiteralPath $fullPath).Length
        [void]$manifest.AppendLine("$normalized`t$size`t$fileHash")
    }

    $treePayload = [System.Text.Encoding]::UTF8.GetBytes($manifest.ToString())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $tree = ([System.BitConverter]::ToString($sha.ComputeHash($treePayload))).Replace('-', '').ToLowerInvariant()
        $payload = [System.Text.Encoding]::UTF8.GetBytes("$head`n$tree")
        $hash = ([System.BitConverter]::ToString($sha.ComputeHash($payload))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }

    return [pscustomobject]@{
        head_sha = $head
        tree_sha = $tree
        snapshot_hash = $hash
    }
}

function Get-ChangedProjectFiles {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $lines = & git -C $ProjectRoot -c core.quotepath=false status --porcelain=v1 --untracked-files=all 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Cannot read changed files for QA artifact.'
    }
    $files = [System.Collections.Generic.List[string]]::new()
    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.Length -lt 4) {
            continue
        }
        $path = $line.Substring(3).Trim()
        if ($path -match ' -> ') {
            $path = ($path -split ' -> ', 2)[1]
        }
        $files.Add($path.Trim('"'))
    }
    return @($files | Sort-Object -Unique)
}

function Write-AtomicUtf8Json {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Value
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $fullDirectory = [System.IO.Path]::GetFullPath($directory)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($fullDirectory + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'QA artifact path escapes its target directory.'
    }
    $temp = Join-Path $directory ('.qa-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $json = $Value | ConvertTo-Json -Depth 12
        [System.IO.File]::WriteAllText($temp, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temp -Destination $fullPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $temp -PathType Leaf) {
            Remove-Item -LiteralPath $temp -Force
        }
    }
}

function Set-QaGatePending {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][ValidateSet('code-review', 'test')][string]$Gate,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $snapshot = Get-ProjectSnapshot -ProjectRoot $ProjectRoot
    $artifactPath = Join-Path $ProjectRoot ".codebuddy/$Gate-pass.flag"
    Write-AtomicUtf8Json -Path $artifactPath -Value ([ordered]@{
        schema_version = 1
        status = 'PENDING'
        gate = $Gate
        agent_role = if ($Gate -eq 'code-review') { 'code_reviewer' } else { 'tester' }
        head_sha = $snapshot.head_sha
        tree_sha = $snapshot.tree_sha
        snapshot_hash = $snapshot.snapshot_hash
        files = @(Get-ChangedProjectFiles -ProjectRoot $ProjectRoot)
        commands = @()
        report = $null
        report_sha256 = $null
        reason = $Reason
        timestamp_utc = [DateTime]::UtcNow.ToString('o')
    })
}

function Get-QaGateFailures {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $snapshot = Get-ProjectSnapshot -ProjectRoot $ProjectRoot
    $failures = [System.Collections.Generic.List[string]]::new()
    foreach ($gate in @('code-review', 'test')) {
        $label = if ($gate -eq 'code-review') { 'Code review' } else { 'Tests' }
        $expectedRole = if ($gate -eq 'code-review') { 'code_reviewer' } else { 'tester' }
        $path = Join-Path $ProjectRoot ".codebuddy/$gate-pass.flag"
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $failures.Add("$label artifact is missing.")
            continue
        }

        try {
            $artifact = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        catch {
            $failures.Add("$label artifact is not valid schema-v1 JSON; rerun the gate.")
            continue
        }

        if ($artifact.schema_version -ne 1 -or [string]$artifact.gate -ne $gate -or [string]$artifact.agent_role -ne $expectedRole) {
            $failures.Add("$label artifact identity or schema is invalid.")
        }
        if ([string]$artifact.status -ne 'PASS') {
            $failures.Add("$label status is '$($artifact.status)'.")
        }
        if ([string]$artifact.head_sha -ne $snapshot.head_sha) {
            $failures.Add("$label was produced for a different HEAD.")
        }
        if ([string]$artifact.snapshot_hash -ne $snapshot.snapshot_hash -or [string]$artifact.tree_sha -ne $snapshot.tree_sha) {
            $failures.Add("$label is stale because the candidate snapshot changed.")
        }
        if ($null -eq $artifact.commands -or @($artifact.commands).Count -eq 0) {
            $failures.Add("$label does not record structured command results.")
        }
        else {
            foreach ($result in @($artifact.commands)) {
                if ($null -eq $result -or [string]::IsNullOrWhiteSpace([string]$result.command) -or $null -eq $result.exit_code -or [int]$result.exit_code -ne 0 -or [string]::IsNullOrWhiteSpace([string]$result.result_summary)) {
                    $failures.Add("$label contains an invalid or failing command result.")
                    break
                }
            }
        }
        if ([string]::IsNullOrWhiteSpace([string]$artifact.report)) {
            $failures.Add("$label does not reference a report.")
        }
        else {
            $reportPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ([string]$artifact.report)))
            if (-not $reportPath.StartsWith($ProjectRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
                $failures.Add("$label report is missing or outside the project root.")
            }
            elseif ([string]::IsNullOrWhiteSpace([string]$artifact.report_sha256) -or [string]$artifact.report_sha256 -ne (Get-FileSha256 -Path $reportPath)) {
                $failures.Add("$label report content changed after the artifact was produced.")
            }
        }
        $parsedTimestamp = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse([string]$artifact.timestamp_utc, [ref]$parsedTimestamp)) {
            $failures.Add("$label timestamp is invalid.")
        }
    }

    $stagedPaths = @(& git -C $ProjectRoot -c core.quotepath=false diff --cached --name-only --diff-filter=ACDMRTUXB 2>$null)
    if ($LASTEXITCODE -ne 0) {
        $failures.Add('Cannot inspect the Git index for staged-file validation.')
    }
    foreach ($stagedPath in $stagedPaths) {
        $normalized = ([string]$stagedPath).Replace('\', '/')
        if ($normalized -match '(?i)(?:^|/)\.env(?:\.|$)' -or
            $normalized -match '(?i)\.(?:db|sqlite|sqlite3|docx|pptx|pdf|mp4|mov|avi)$' -or
            $normalized -match '(?i)^(?:materials|\.workbuddy/memory|docs|_scratch)/' -or
            $normalized -match '(?i)^\.codebuddy/.*\.flag$') {
            $failures.Add("Sensitive or excluded file is staged: $normalized")
            continue
        }
        & git -C $ProjectRoot diff --quiet -- $normalized 2>$null
        $worktreeDiffExit = $LASTEXITCODE
        if ($worktreeDiffExit -eq 1) {
            $failures.Add("Staged content differs from the reviewed working-tree content: $normalized")
        }
        elseif ($worktreeDiffExit -ne 0) {
            $failures.Add("Cannot compare staged and working-tree content: $normalized")
        }
    }
    return @($failures)
}
