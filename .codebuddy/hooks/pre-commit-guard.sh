#!/bin/bash
# Hook 1: Git 提交前拦截 + 质检测试门禁
# 事件: PreToolUse (matcher: Bash)
# 逻辑: 检测 git commit 命令，检查两个独立标记文件：
#   - code-review-pass.flag（由代码检查 subagent 完成且通过后写入）
#   - test-pass.flag（由测试 subagent 完成且通过后写入）
#   两个都存在且内容为 PASS 才放行，否则阻止并精确告知缺哪个

set -e

# 从 stdin 读取 hook 输入 JSON
INPUT=$(cat)

# 解析 tool_input.command 字段
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

# 如果不是 git commit 命令，直接放行
if ! echo "$COMMAND" | grep -qE 'git\s+commit'; then
  exit 0
fi

# 是 git commit 命令，检查两个独立标记文件
FLAGS_DIR="$CODEBUDDY_PROJECT_DIR/.codebuddy"

CODE_REVIEW_FLAG="$FLAGS_DIR/code-review-pass.flag"
TEST_FLAG="$FLAGS_DIR/test-pass.flag"

# 收集缺失/未通过的标记
MISSING=()

# 检查代码检查标记
if [ ! -f "$CODE_REVIEW_FLAG" ]; then
  MISSING+=("代码检查（code-review-pass.flag 不存在，请先派生代码检查 subagent）")
elif [ "$(cat "$CODE_REVIEW_FLAG" | tr -d '[:space:]')" != "PASS" ]; then
  MISSING+=("代码检查（code-review-pass.flag 内容非 PASS）")
fi

# 检查测试标记
if [ ! -f "$TEST_FLAG" ]; then
  MISSING+=("测试（test-pass.flag 不存在，请先派生测试 subagent）")
elif [ "$(cat "$TEST_FLAG" | tr -d '[:space:]')" != "PASS" ]; then
  MISSING+=("测试（test-pass.flag 内容非 PASS）")
fi

# 如果有缺失/未通过的标记，阻止提交并精确告知
if [ ${#MISSING[@]} -gt 0 ]; then
  REASONS=$(printf ' - %s\n' "${MISSING[@]}")
  cat <<EOF
{"reason":"提交被拦截，以下检查未通过：\n$REASONS\n\n请先派生对应 subagent 完成检查，subagent 通过后会自动写入各自的 PASS 标记。两个标记都就绪后再提交。"}
EOF
  exit 2
fi

# 两个标记都是 PASS，放行提交
exit 0
