#!/bin/bash
# Hook 2: 需求变更拦截 → 注入上下文提醒调用需求 subagent
# 事件: UserPromptSubmit
# 逻辑: 检测用户 prompt 含需求变更关键词，注入 additionalContext 提醒主 Agent 先走需求评审

set -e

# 从 stdin 读取 hook 输入 JSON
INPUT=$(cat)

# 解析 prompt 字段
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)

# 需求变更关键词列表
KEYWORDS="需求变更|需求修改|改需求|改功能|改范围|requirement change|scope change|改一下需求|调整需求|需求调整"

# 关键词匹配（不区分大小写）
if echo "$PROMPT" | grep -qiE "$KEYWORDS"; then
  # 命中需求变更信号，注入上下文（不阻塞，退出码 0）
  cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"⚠️ 检测到需求变更信号。请先派生「需求评审」subagent 对变更进行澄清、评审、优先级排序，更新 _scratch/需求评审报告.md 和对应文档后，再执行变更。不要跳过需求评审直接改代码。"}}
EOF
  exit 0
fi

# 未命中，静默放行
exit 0
