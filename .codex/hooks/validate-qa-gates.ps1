param(
    [string]$ProjectRoot = ''
)

. (Join-Path $PSScriptRoot 'hook-utils.ps1')

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Get-ProjectRoot ([pscustomobject]@{ cwd = (Get-Location).Path })
}
else {
    $ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
}

$failures = @(Get-QaGateFailures -ProjectRoot $ProjectRoot)
if ($failures.Count -gt 0) {
    [Console]::Error.WriteLine("Commit blocked by the project QA gate:")
    foreach ($failure in $failures) {
        [Console]::Error.WriteLine(" - $failure")
    }
    [Console]::Error.WriteLine('Rerun code_reviewer and tester on the unchanged candidate snapshot.')
    exit 1
}

Write-Output 'QA gate PASS: code review and tests match the current candidate snapshot.'
exit 0
