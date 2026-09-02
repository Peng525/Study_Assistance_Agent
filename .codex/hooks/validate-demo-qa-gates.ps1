param([string]$ProjectRoot = '')

. (Join-Path $PSScriptRoot 'demo-hook-utils.ps1')

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Get-ProjectRoot ([pscustomobject]@{ cwd = (Get-Location).Path })
}

$failures = @(Get-DemoQaGateFailures -ProjectRoot $ProjectRoot)
if ($failures.Count -gt 0) {
    [Console]::Error.WriteLine('Commit blocked by the Demo QA gate:')
    foreach ($failure in $failures) {
        [Console]::Error.WriteLine(" - $failure")
    }
    [Console]::Error.WriteLine('Run tester first, then code_reviewer, on the unchanged business/test snapshot.')
    exit 1
}

Write-Output 'Demo QA PASS: targeted unit tests and lightweight code review match the current snapshot.'
exit 0
