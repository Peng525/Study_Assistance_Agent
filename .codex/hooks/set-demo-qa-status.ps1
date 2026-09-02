param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('code-review', 'test')]
    [string]$Gate,

    [Parameter(Mandatory = $true)]
    [ValidateSet('PASS', 'PENDING')]
    [string]$Status,

    [Parameter(Mandatory = $true)]
    [string]$Summary,

    [string]$Report = '',
    [string]$ProjectRoot = ''
)

. (Join-Path $PSScriptRoot 'demo-hook-utils.ps1')

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Get-ProjectRoot ([pscustomobject]@{ cwd = (Get-Location).Path })
}

$artifact = Write-DemoQaArtifact -ProjectRoot $ProjectRoot -Gate $Gate -Status $Status -Summary $Summary -Report $Report
Write-Output "$Gate Demo gate is $($artifact.status) for snapshot $($artifact.snapshot_hash)."
