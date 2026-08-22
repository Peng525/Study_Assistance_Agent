#!/bin/bash
# Hook 5: PostToolUse 代码轻检
# 事件: PostToolUse (matcher: Write|Edit)
# 逻辑: 写入/编辑 src/ 下的 .ts/.tsx/.js/.jsx 文件后，运行 TypeScript 类型检查或 ESLint

set -e

PROJECT_DIR="$CODEBUDDY_PROJECT_DIR"

# 从 stdin 读取 hook 输入 JSON
INPUT=$(cat)

# 解析 tool_input.file_path
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# 如果没有文件路径，静默放行
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# 检查是否是 src/ 下的代码文件
if ! echo "$FILE_PATH" | grep -qE '/src/.*\.(ts|tsx|js|jsx)$'; then
  exit 0
fi

# 检查项目是否配置了 TypeScript
if [ -f "$PROJECT_DIR/tsconfig.json" ]; then
  # 运行 TypeScript 类型检查（只检查当前文件相关的错误）
  TSC_OUTPUT=$(cd "$PROJECT_DIR" && npx tsc --noEmit 2>&1) || true

  # 过滤出当前文件的错误
  FILE_ERRORS=$(echo "$TSC_OUTPUT" | grep -i "$(basename "$FILE_PATH")" 2>/dev/null) || true

  if [ -n "$FILE_ERRORS" ]; then
    # 有类型错误，注入上下文（不阻塞，退出码 0）
    ERROR_MSG=$(echo "$FILE_ERRORS" | head -10)
    MESSAGE="⚠️ TypeScript 类型检查发现以下问题（$(basename "$FILE_PATH")）：\n$ERROR_MSG"
    echo "$MESSAGE" | jq -Rs '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: .}}'
    exit 0
  fi
fi

# 检查项目是否配置了 ESLint
if [ -f "$PROJECT_DIR/.eslintrc.js" ] || [ -f "$PROJECT_DIR/.eslintrc.json" ] || [ -f "$PROJECT_DIR/eslint.config.js" ]; then
  ESLINT_OUTPUT=$(cd "$PROJECT_DIR" && npx eslint "$FILE_PATH" 2>&1) || true

  if [ -n "$ESLINT_OUTPUT" ]; then
    MESSAGE="⚠️ ESLint 检查发现以下问题（$(basename "$FILE_PATH")）：\n$(echo "$ESLINT_OUTPUT" | head -10)"
    echo "$MESSAGE" | jq -Rs '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: .}}'
    exit 0
  fi
fi

# 无错误或未配置检查工具，静默放行
exit 0
