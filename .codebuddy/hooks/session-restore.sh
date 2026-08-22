#!/bin/bash
# Hook 3: SessionStart 会话恢复
# 事件: SessionStart (所有 source: startup/resume/clear/compact)
# 逻辑: 读取项目记忆、当日日志、_scratch 产出列表、CHANGELOG，拼接为 additionalContext 注入上下文

set -e

PROJECT_DIR="$CODEBUDDY_PROJECT_DIR"
MEMORY_DIR="$PROJECT_DIR/.workbuddy/memory"

# 获取今天日期
TODAY=$(date '+%Y-%m-%d')

# 收集上下文片段
CONTEXT=""

# 1. 长期记忆
if [ -f "$MEMORY_DIR/MEMORY.md" ]; then
  CONTEXT="$CONTEXT\n\n=== 项目长期记忆 ===\n$(cat "$MEMORY_DIR/MEMORY.md")"
fi

# 2. 当日工作日志
if [ -f "$MEMORY_DIR/$TODAY.md" ]; then
  CONTEXT="$CONTEXT\n\n=== 今日工作日志 ($TODAY) ===\n$(cat "$MEMORY_DIR/$TODAY.md")"
fi

# 3. _scratch 产出文件列表
if [ -d "$PROJECT_DIR/_scratch" ]; then
  SCRATCH_FILES=$(ls -1 "$PROJECT_DIR/_scratch/"*.md 2>/dev/null | xargs -I{} basename {} 2>/dev/null)
  if [ -n "$SCRATCH_FILES" ]; then
    CONTEXT="$CONTEXT\n\n=== _scratch 已有产出 ===\n$SCRATCH_FILES"
  fi
fi

# 4. CHANGELOG
if [ -f "$PROJECT_DIR/CHANGELOG.md" ]; then
  # 只读前 30 行避免过长
  CHANGELOG_HEAD=$(head -30 "$PROJECT_DIR/CHANGELOG.md")
  CONTEXT="$CONTEXT\n\n=== CHANGELOG (最近) ===\n$CHANGELOG_HEAD"
fi

# 5. compact 快照（如果存在，说明上次压缩前保存了状态）
if [ -f "$MEMORY_DIR/compact-snapshot.md" ]; then
  CONTEXT="$CONTEXT\n\n=== 上次压缩前状态快照 ===\n$(cat "$MEMORY_DIR/compact-snapshot.md")"
fi

# 如果没有任何上下文，静默退出
if [ -z "$CONTEXT" ]; then
  exit 0
fi

# 输出 additionalContext
# 使用 jq 构造合法 JSON，避免特殊字符问题
MESSAGE="📋 会话恢复：已自动加载项目上下文。$CONTEXT"
echo "$MESSAGE" | jq -Rs '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: .}}'
exit 0
