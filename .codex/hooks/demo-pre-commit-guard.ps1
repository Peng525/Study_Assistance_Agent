. (Join-Path $PSScriptRoot 'demo-hook-utils.ps1')

$hookInput = Read-HookInput
$command = Get-HookCommand $hookInput

if ($command -notmatch '(?is)(?:^|[\\/\s\"'';&|])git(?:\.exe)?(?:[\"''])?(?:\s+-C\s+(?:\"[^\"]+\"|''[^'']+''|\S+))*\s+commit\b') {
    exit 0
}

$root = Get-ProjectRoot $hookInput
$failures = @(Get-DemoQaGateFailures -ProjectRoot $root)
if ($failures.Count -gt 0) {
    $reason = "Commit blocked by the lightweight Demo QA gate. Run tester, then code_reviewer:`n - " + ($failures -join "`n - ")
    Write-HookJson @{
        hookSpecificOutput = @{
            hookEventName = 'PreToolUse'
            permissionDecision = 'deny'
            permissionDecisionReason = $reason
        }
    }
}

exit 0
