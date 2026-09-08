. (Join-Path $PSScriptRoot 'hook-utils.ps1')

# Reminder-only hook: nudges the agent to update the handover doc after
# business code / tests / dependency changes. Never blocks, never fails the tool call.
#
# NOTE: this file must stay ASCII-only. PowerShell 5.1 on this machine has a
# history of mangling CJK literals in .ps1 files (see demo-hook-utils.ps1), so the
# handover markdown file name is resolved dynamically instead of being hardcoded.

try {
    $hookInput = Read-HookInput
    $root = Get-ProjectRoot $hookInput

    $handoverDirRel = 'docs/00-handover'
    $stateRel = '_scratch/.handover-reminder.json'
    $cooldownMinutes = 20
    $finishCooldownMinutes = 5

    $inputText = ''
    if ($hookInput.PSObject.Properties.Name -contains 'tool_input') {
        $inputText = $hookInput.tool_input | ConvertTo-Json -Depth 20 -Compress
    }
    $command = Get-HookCommand $hookInput
    $toolName = if ($hookInput.PSObject.Properties.Name -contains 'tool_name') { [string]$hookInput.tool_name } else { '' }

    $isFileEdit = ($toolName -eq 'Edit' -or $toolName -eq 'Write' -or $toolName -eq 'apply_patch')

    $touchesBackend    = $inputText -match '(?i)backend[\\/]+app[\\/]+[^\"\r\n]+\.py'
    $touchesFrontend   = $inputText -match '(?i)frontend[\\/]+src[\\/]+[^\"\r\n]+\.(ts|tsx|js|jsx)'
    $touchesTests      = $inputText -match '(?i)(backend[\\/]+tests|frontend[\\/]+src[\\/]+test)[\\/]'
    $touchesDeps       = $inputText -match '(?i)(package(?:-lock)?\.json|requirements[^\\/\"'']*\.txt|vite\.config\.[a-z]+)'
    $touchesRuntime    = $inputText -match '(?i)(^|[\\/\"''])start\.py'
    $touchesHandover   = $inputText -match '(?i)(^|[\\/\"''])00-handover[\\/]'
    $touchesDocsOnly   = $inputText -match '(?i)(^|[\\/\"''])(docs|_scratch)[\\/]'
    $touchesGovernance = $inputText -match '(?i)(?:^|[\s\\/\"''])((?:\.codex[\\/]+)|\.githooks[\\/]|AGENTS\.md)'

    $relevantEdit = $isFileEdit -and ($touchesBackend -or $touchesFrontend -or $touchesTests -or $touchesDeps -or $touchesRuntime)

    # Editing the handover doc itself, docs, or governance files is not a signal
    # that the handover doc needs updating.
    if ($isFileEdit -and -not $relevantEdit) {
        exit 0
    }
    if ($isFileEdit -and $touchesHandover) {
        exit 0
    }
    if ($isFileEdit -and ($touchesDocsOnly -or $touchesGovernance) -and -not ($touchesBackend -or $touchesFrontend)) {
        exit 0
    }

    # Completion signals: tests, build, commit. Shorter cooldown, these mark real milestones.
    $finishSignal = ($toolName -eq 'Bash') -and ($command -match '(?i)(pytest|npm\s+test|npm\s+run\s+build|git\s+commit)')

    if (-not $relevantEdit -and -not $finishSignal) {
        exit 0
    }

    $statePath = Join-Path $root $stateRel
    $now = [DateTime]::UtcNow
    $lastEdit = [DateTime]::MinValue
    $lastFinish = [DateTime]::MinValue
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try {
            $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
            $parsedEdit = [DateTime]::MinValue
            $parsedFinish = [DateTime]::MinValue
            if ([DateTime]::TryParse([string]$state.last_edit_at, [ref]$parsedEdit)) { $lastEdit = $parsedEdit }
            if ([DateTime]::TryParse([string]$state.last_finish_at, [ref]$parsedFinish)) { $lastFinish = $parsedFinish }
        }
        catch {
            $lastEdit = [DateTime]::MinValue
            $lastFinish = [DateTime]::MinValue
        }
    }

    # Edit reminders and finish-signal reminders run on independent clocks. A finish
    # signal (tests / build / commit) is a real milestone and must not be silenced
    # just because an edit reminder fired a moment ago.
    if ($finishSignal) {
        if (($now - $lastFinish).TotalMinutes -lt $finishCooldownMinutes) { exit 0 }
    }
    else {
        if (($now - $lastEdit).TotalMinutes -lt $cooldownMinutes) { exit 0 }
    }

    $stateDir = Split-Path -Parent $statePath
    if (-not (Test-Path -LiteralPath $stateDir)) {
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
    }
    $payload = @{
        last_edit_at = if ($finishSignal) { $lastEdit.ToString('o') } else { $now.ToString('o') }
        last_finish_at = if ($finishSignal) { $now.ToString('o') } else { $lastFinish.ToString('o') }
        tool = $toolName
        reason = if ($finishSignal) { 'finish' } else { 'edit' }
    }
    [System.IO.File]::WriteAllText(
        $statePath,
        (($payload | ConvertTo-Json -Compress) + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false))

    $scope = [System.Collections.Generic.List[string]]::new()
    if ($touchesBackend) { $scope.Add('backend/app') }
    if ($touchesFrontend) { $scope.Add('frontend/src') }
    if ($touchesTests) { $scope.Add('tests') }
    if ($touchesDeps -or $touchesRuntime) { $scope.Add('deps/runtime') }
    if ($finishSignal) { $scope.Add('finish-signal') }

    # Resolve the doc directory without hardcoding its CJK file name.
    $handoverDir = Join-Path $root $handoverDirRel
    $target = if (Test-Path -LiteralPath $handoverDir -PathType Container) {
        $handoverDirRel + '/'
    }
    else {
        $handoverDirRel + '/ (MISSING)'
    }

    $context = '[Handover reminder] Change detected in: ' + ($scope -join ', ') + ".`n" +
        'Once this change is verified, update the handover doc under ' + $target + "`n" +
        '  - Ch.4 requirement completion status`n' +
        '  - Ch.5 todo list (status + actual finish date)`n' +
        '  - Ch.6.2 update log (time + commit + one-line summary)`n' +
        'Reminder only. Does not block commit.'

    Write-HookJson (New-AdditionalContextOutput -EventName 'PostToolUse' -Context $context)
}
catch {
    # A reminder hook must never interfere with the main tool call.
}

exit 0
