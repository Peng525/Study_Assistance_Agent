#!/bin/bash
# Hook 4: PreCompact 状态保存
# 事件: PreCompact (manual 和 auto 都触发)
# 逻辑: 压缩前把当前项目状态快照写入记忆文件，防止压缩丢失关键信息

set -e

PROJECT_DIR="$CODEBUDDY_PROJECT_DIR"
MEMORY_DIR="$PROJECT_DIR/.workbuddy/memory"
SNAPSHOT_FILE="$MEMORY_DIR/compact-snapshot.md"
TODAY=$(date '+%Y-%m-%d %H:%M:%S')

# 从 stdin 读取 hook 输入（含 trigger: manual/auto）
INPUT=$(cat)
TRIGGER=$(echo "$INPUT" | jq -r '.trigger // "unknown"' 2>/dev/null)

# 确保记忆目录存在
mkdir -p "$MEMORY_DIR"

# 收集当前状态
CURRENT_MEMORY=""
if [ -f "$MEMORY_DIR/MEMORY.md" ]; then
  CURRENT_MEMORY=$(cat "$MEMORY_DIR/MEMORY.md")
fi

CURRENT_LOG=""
if [ -f "$MEMORY_DIR/$TODAY_SHORT.md" ]; then
  TODAY_SHORT=$(date '+%Y-%m-%d')
  if [ -f "$MEMORY_DIR/$TODAY_SHORT.md" ]; then
    CURRENT_LOG=$(cat "$MEMORY_DIR/$TODAY_SHORT.md")
  fi
fi

SCRATCH_LIST=""
if [ -d "$PROJECT_DIR/_scratch" ]; then
  SCRATCH_LIST=$(ls -1 "$PROJECT_DIR/_scratch/"*.md 2>/dev/null | xargs -I{} basename {} 2>/dev/null)
fi

# 写入状态快照
cat > "$SNAPSHOT_FILE" << EOF
# 压缩前状态快照

> 生成时间：$TODAY
> 触发方式：$TRIGGER
> 用途：上下文压缩前的状态保存，压缩后由 SessionStart hook 自动读回

## 项目长期记忆
$CURRENT_MEMORY

## 今日工作日志
$CURRENT_LOG

## _scratch 已有产出
$SCRATCH_LIST

## 当前 Phase
Phase 0（AI 助学核心功能验证）

## 关键待办
- P0-1 范围错位重评（风险登记册v2）✅
- P0-2 管理台设计（架构师评估）⏳
- P0-3 可换素材承载方式（架构师评估）⏳
- P0-4 Token超限策略（PRD细化）⏳
- P0-5 字幕格式容错（PRD细化）⏳
- Hooks 创建 ✅
- 架构评估 ⏳
- PRD 深化 ⏳
- 项目搭建 ⏳
- 功能开发 ⏳
EOF

# 放行压缩
exit 0
