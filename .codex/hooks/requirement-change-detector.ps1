. (Join-Path $PSScriptRoot 'hook-utils.ps1')

$hookInput = Read-HookInput
$prompt = ''
if ($hookInput.PSObject.Properties.Name -contains 'prompt') {
    $prompt = [string]$hookInput.prompt
}

$patternBytes = [Convert]::FromBase64String('6ZyA5rGC5Y+Y5pu0fOmcgOaxguS/ruaUuXzmlLnpnIDmsYJ85pS55Yqf6IO9fOaUueiMg+WbtHzpnIDmsYLosIPmlbR86LCD5pW06ZyA5rGCfHNjb3BlXHMrY2hhbmdlfHJlcXVpcmVtZW50XHMrY2hhbmdl')
$pattern = [Text.Encoding]::UTF8.GetString($patternBytes)
$naturalChangePattern = '(?i)(新增|增加|支持|改成|换成|替换|删除|移除|调整|扩展|重构|下一阶段|后续迭代|兼容|deprecat|add\s+support|replace|remove|extend|refactor)'
if ($prompt -match $pattern -or $prompt -match $naturalChangePattern) {
    $context = @'
Requirement-change signal detected. First decide whether this is a bug fix or a change to scope or acceptance criteria. For a real requirement change, use the requirement_reviewer subagent before implementation, then use prd_writer or architect when needed. Do not expand product scope without review.
'@
    Write-HookJson (New-AdditionalContextOutput -EventName 'UserPromptSubmit' -Context $context.Trim())
}

exit 0
