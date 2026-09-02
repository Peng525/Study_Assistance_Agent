. (Join-Path $PSScriptRoot 'hook-utils.ps1')

$hookInput = Read-HookInput
$command = Get-HookCommand $hookInput

if ($command -notmatch '(?is)(?:^|[\\/\s\"'';&|])git(?:\.exe)?(?:[\"''])?(?:\s+-C\s+(?:\"[^\"]+\"|''[^'']+''|\S+))*\s+commit\b') {
    exit 0
}

$root = Get-ProjectRoot $hookInput
$missing = @(Get-QaGateFailures -ProjectRoot $root)

if ($missing.Count -gt 0) {
    $reason = "Commit blocked by the project QA gate. Rerun code_reviewer and tester on the unchanged candidate snapshot:`n - " + ($missing -join "`n - ")
    Write-HookJson @{
        hookSpecificOutput = @{
            hookEventName = 'PreToolUse'
            permissionDecision = 'deny'
            permissionDecisionReason = $reason
        }
    }
}

exit 0
