. (Join-Path $PSScriptRoot 'hook-utils.ps1')

$hookInput = Read-HookInput
$agentType = if ($hookInput.PSObject.Properties.Name -contains 'agent_type') { [string]$hookInput.agent_type } else { 'unknown' }
$roleContext = switch ($agentType) {
    'architect' { 'Use the principle: minimal implementation inside stable evolution boundaries. Cover Current, Next, Later, contracts, migration triggers, ADRs and accepted technical debt.' }
    'requirement_reviewer' { 'Classify against the current implementation, assign stable requirement and acceptance IDs, and hand off explicit product, architecture and QA constraints.' }
    'prd_writer' { 'Do not preserve Phase 0 as a permanent ceiling. Maintain Current/Next/Later status and end-to-end requirement-to-test traceability.' }
    'code_reviewer' { 'Invalidate the old review artifact first. Review the entire unchanged candidate snapshot and produce a traceable report before PASS.' }
    'tester' { 'Invalidate the old test artifact first. Build a requirement/risk coverage matrix and record every final verification command before PASS.' }
    'release_manager' { 'Treat commit, push, tag, PR and release as separate permissions. Reject stale QA artifacts and unauthorized files.' }
    default { 'Follow the role-specific prompt and AGENTS.md.' }
}

$context = @"
Project subagent contract reminder:
- Phase 0 core functionality is already implemented; verify current code and git diff before relying on older documents.
- The current user instruction outranks restored memory. Separate facts, assumptions, decisions and open questions.
- Deliver the current outcome completely while preserving evidence-based Phase 1+/2 contracts and migration seams; do not speculate infrastructure without triggers.
- Do not read or expose .env or secrets, and stay inside the role write allowlist.
- $roleContext
"@

Write-HookJson (New-AdditionalContextOutput -EventName 'SubagentStart' -Context $context.Trim())
exit 0
