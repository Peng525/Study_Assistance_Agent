. (Join-Path $PSScriptRoot 'hook-utils.ps1')

$hookInput = Read-HookInput
$root = Get-ProjectRoot $hookInput
$inputText = ''
if ($hookInput.PSObject.Properties.Name -contains 'tool_input') {
    $inputText = $hookInput.tool_input | ConvertTo-Json -Depth 20 -Compress
}

$messages = [System.Collections.Generic.List[string]]::new()
$touchesFrontend = $inputText -match '(?i)frontend[\\/]+src[\\/]+[^\"\r\n]+\.(ts|tsx|js|jsx)'
$touchesBackend = $inputText -match '(?i)backend[\\/]+app[\\/]+[^\"\r\n]+\.py'
$touchesTests = $inputText -match '(?i)(backend[\\/]+tests|frontend[\\/]+src[\\/]+test)[\\/]'
$touchesGovernance = $inputText -match '(?i)(?:^|[\s\\/\"''])((?:\.codex[\\/]+(?:agents|hooks)[\\/])|(?:\.codex[\\/]+(?:config\.toml|hooks\.json))|(?:\.githooks[\\/])|AGENTS\.md)'
$touchesDependencies = $inputText -match '(?i)(package(?:-lock)?\.json|requirements[^\\/\"'']*\.txt|pyproject\.toml|vite\.config\.[a-z]+|tsconfig[^\\/\"'']*\.json)'
$command = Get-HookCommand $hookInput
$shellMayWrite = $command -match '(?i)(--write|--fix|\bformat\b|\bapply\b|\binstall\b)'
$toolName = if ($hookInput.PSObject.Properties.Name -contains 'tool_name') { [string]$hookInput.tool_name } else { '' }
$isFileEdit = $toolName -eq 'apply_patch' -or $toolName -eq 'Edit' -or $toolName -eq 'Write'
$shouldInvalidateQa = ($isFileEdit -and ($touchesFrontend -or $touchesBackend -or $touchesTests -or $touchesGovernance -or $touchesDependencies)) -or ($toolName -eq 'Bash' -and $shellMayWrite)

if ($shouldInvalidateQa) {
    $reason = 'Candidate files changed after the previous QA result.'
    Set-QaGatePending -ProjectRoot $root -Gate 'code-review' -Reason $reason
    Set-QaGatePending -ProjectRoot $root -Gate 'test' -Reason $reason
    $messages.Add('QA gates were reset to PENDING because the candidate snapshot changed.')
}

if ($touchesFrontend) {
    $frontend = Join-Path $root 'frontend'
    $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npm -and (Test-Path -LiteralPath 'D:\devolop\node\npm.cmd')) {
        $npm = 'D:\devolop\node\npm.cmd'
    }
    if ($npm -and (Test-Path -LiteralPath (Join-Path $frontend 'tsconfig.json'))) {
        Push-Location $frontend
        try {
            $output = (& $npm exec tsc -- --noEmit 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                $messages.Add("TypeScript check failed:`n" + (($output -split "`n" | Select-Object -First 20) -join "`n"))
            }
        }
        finally {
            Pop-Location
        }
    }
}

if ($touchesBackend) {
    $backend = Join-Path $root 'backend'
    $python = Join-Path $backend 'venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    }
    if ($python) {
        $output = (& $python -m compileall -q (Join-Path $backend 'app') 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0) {
            $messages.Add("Python compile check failed:`n" + (($output -split "`n" | Select-Object -First 20) -join "`n"))
        }
    }
}

if ($messages.Count -gt 0) {
    $context = "Post-change project checks:`n`n" + ($messages -join "`n`n")
    Write-HookJson (New-AdditionalContextOutput -EventName 'PostToolUse' -Context $context)
}

exit 0
