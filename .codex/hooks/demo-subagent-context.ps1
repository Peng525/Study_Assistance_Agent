. (Join-Path $PSScriptRoot 'hook-utils.ps1')

$hookInput = Read-HookInput
$agentType = if ($hookInput.PSObject.Properties.Name -contains 'agent_type') { [string]$hookInput.agent_type } else { 'unknown' }
$roleContext = switch ($agentType) {
    'code_reviewer' { 'Run after tester. Review only the current diff for obvious behavior errors, exception messages, secret leakage, misleading comments, and test weakening. Update the shared Demo test record defined in AGENTS.md.' }
    'tester' { 'Run first. Execute only directly related unit tests, add only essential regression tests, and open the shared Demo test record entry defined in AGENTS.md.' }
    default { 'Follow the role-specific prompt and AGENTS.md.' }
}

$context = @"
Demo-stage subagent reminder:
- This is a local demonstrable product, not a production release audit.
- Read the current user instruction and git diff first; do not expand into a full requirement, architecture or risk matrix unless explicitly requested.
- tester and code_reviewer run serially and only maintain the shared Demo test record defined in AGENTS.md plus their own lightweight flag.
- Environment blockage or an unverified command remains PENDING and blocks commit.
- Do not read or expose .env or secrets.
- $roleContext
"@

Write-HookJson (New-AdditionalContextOutput -EventName 'SubagentStart' -Context $context.Trim())
exit 0
