# AI 助学项目文档导航

> 项目：AI 助学产品（B站知识视频AI助学助手）
> 仓库：https://github.com/Peng525/Study_Assistance_Agent
> 当前阶段：Phase 0（跑通AI助学核心功能）

## 文档体系结构

```
docs/
├── 01-requirements/    需求类：提议书、PRD、需求变更
├── 02-design/          设计类：架构设计、技术调研
├── 03-development/     开发类：环境配置、开发规范
├── 04-quality/         质量类：评审报告、风险登记册
├── 05-delivery/        交付类：部署文档、用户手册（后续创建）
└── 06-management/      管理类：技术准备方案、工作流规范
```

## 按角色导航

### 产品/需求人员
- [项目提议书](01-requirements/项目提议书.docx) - 原始需求（B站评审用）
- [灵感来源](01-requirements/灵感来源.txt) - 用户原始场景描述
- [PRD](01-requirements/prd.md) - 开发级产品需求文档（11章，含用户故事和验收标准）
- [范围变更说明](01-requirements/范围变更说明.md) - 提议书V1.2→升级后Phase 0差异
- [需求变更记录](01-requirements/需求变更记录-001.md) - 6项变更记录

### 架构/设计人员
- [架构设计 v1](02-design/architecture.md) - 技术栈选型+架构图+RAG决策+字幕选中方案
- [架构设计 v2](02-design/architecture-v2.md) - 补4个盲点：用户系统+PPT+素材更新+Whisper
- [字幕技术调研](02-design/research/字幕技术调研.md) - 字幕来源/格式/工具/选型决策

### 开发人员
- [开发环境配置](03-development/开发环境配置.md) - Python/Node/ffmpeg/.env配置
- [开发规范](03-development/开发规范.md) - 代码规范+git规范+分支策略
- [技术准备方案](06-management/项目技术准备方案.md) - v4，技术栈+subagent体系+工作流

### 质量人员
- [需求评审报告](04-quality/需求评审报告.md) - 5个致命项+6个重要项
- [风险登记册](04-quality/风险登记册.md) - 10个风险项+等级+降级预案

### 项目管理
- [QA-PASS机制说明](06-management/qa-pass机制说明.md) - 双标记各自背书的git提交门禁

## 项目阶段

| 阶段 | 目标 | 状态 |
|---|---|---|
| Phase 0a 需求评审 | 拷问提议书，挖出致命项 | ✅ 完成 |
| Phase 0c 架构评估 | 技术栈选型+架构设计 | ✅ 完成（v1+v2） |
| Phase 0b PRD深化 | 开发级PRD | ✅ 完成 |
| Phase 0d 项目搭建 | 初始化前后端骨架 | ⏳ 待执行 |
| Phase 0e 功能开发 | 核心链路编码 | ⏳ |
| Phase 0f 代码检查 | 代码审查 | ⏳ |
| Phase 0g 测试验证 | E2E测试 | ⏳ |
| Phase 0h 版本管理 | git提交+CHANGELOG | ⏳ |
| Phase 0i 交付 | 录屏+交付物 | ⏳ |

## 技术栈速查

| 层 | 选型 |
|---|---|
| 前端 | React 18 + Vite + Ant Design 5 + Zustand + ArtPlayer |
| 后端 | Python FastAPI + SQLite + SQLAlchemy |
| 大模型 | 阿里云百炼平台（通义千问），OpenAI兼容协议 |
| 字幕生成 | OpenAI Whisper（medium模型，本地生成） |
| 上下文策略 | 时间窗截取（字幕±3分钟）+ 课件章节粗筛（不上RAG） |
| 部署 | 本地开发模式，后端绑定127.0.0.1 |
