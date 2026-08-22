# Agent.md · AI 助学项目

> 项目入口文档 | 最后更新：2026-08-23
> 任何 agent 或人加入项目时，先读这个文件

---

## 一、项目基本情况

### 项目名称
AI 助学助手（B 站知识视频 AI 助学）

### 一句话定位
让学习者在视频播放现场直接提问，AI 结合课程字幕和课件上下文即时答疑，不用跳转到外部 AI 工具。

### 项目阶段
- **Phase 0（当前）**：跑通核心功能——视频→暂停→选中字幕→右键→AI 答疑 + 管理台配置大模型 + 可换素材 + 用户系统简化版 + Whisper 自动字幕
- **Phase 1+（后续）**：完整学习平台（视频上架/栏目/历史/上传/用户注册/RAG 语义检索）

### 交付对象
B 站评审 + 自用（两者都是）

### 仓库地址
https://github.com/Peng525/Study_Assistance_Agent

---

## 二、技术栈

### 前端
| 组件 | 选型 | 说明 |
|---|---|---|
| 框架 | React 18 + Vite | SPA，不上 Next.js（Phase 1+ 可平滑迁移） |
| UI 库 | Ant Design 5 | 企业级中文社区首选 |
| 状态管理 | Zustand | 轻量，流式输出时不触发全页 re-render |
| 播放器 | ArtPlayer.js + 自定义字幕渲染层 | 不用原生 `<track>`（跨浏览器选中不可控） |
| 类型 | TypeScript | 全量类型注解 |

### 后端
| 组件 | 选型 | 说明 |
|---|---|---|
| 框架 | Python FastAPI | 原生 async + SSE 流式转发，本机已有 Python 3.13 |
| 数据库 | SQLite + SQLAlchemy | 零运维单文件，Phase 1+ 可迁 PostgreSQL |
| 大模型代理 | httpx | 后端代理所有大模型请求，key 不暴露前端 |
| 字幕生成 | OpenAI Whisper（medium 模型） | 本地生成，零调用成本 |
| 字幕解析 | webvtt-py / pysrt | SRT/VTT 解析与转换 |
| 课件提取 | pymupdf（PDF）/ python-pptx（PPT） | 纯文本提取缓存 |

### 大模型
- 平台：阿里云百炼平台（通义千问）
- 协议：OpenAI 兼容协议
- 配置方式：管理台配置（key/baseUrl/模型名），后端代理

### 上下文策略
- Phase 0：时间窗截取（字幕±3 分钟）+ 课件章节粗筛，**不上 RAG**
- Phase 1+：引入 RAG（Chroma + bge-m3），context_builder 解耦可替换
- Phase 2+：引入 langchain（需要 Agent/工具调用/多步推理时）

### 部署
- 本地开发模式，后端绑定 127.0.0.1，不上 Docker
- 前端 Vite proxy 代理 /api 到后端 8000

---

## 三、文档体系与放置规则

### 文档目录结构
```
docs/
├── README.md                ← 文档导航索引（按角色导航）
├── 01-requirements/         ← 需求类
│   ├── 项目提议书.docx        ← 原始需求（B站评审用）
│   ├── 灵感来源.txt           ← 用户原始场景描述
│   ├── prd.md               ← 开发级 PRD（用户故事+验收标准+API清单+数据模型）
│   ├── 范围变更说明.md         ← 提议书 V1.2 → 升级后 Phase 0 差异
│   └── 需求变更记录-001.md    ← 6 项变更记录
├── 02-design/               ← 设计类
│   ├── architecture.md      ← 架构设计 v1（技术栈选型+架构图+RAG决策+字幕方案）
│   ├── architecture-v2.md   ← 架构设计 v2（补4个盲点：用户系统+PPT+素材更新+Whisper）
│   └── research/            ← 技术调研
│       └── 字幕技术调研.md    ← 字幕来源/格式/工具对比/选型决策
├── 03-development/          ← 开发类
│   ├── 开发环境配置.md        ← Python/Node/ffmpeg/.env 配置
│   └── 开发规范.md            ← 代码规范+git规范+分支策略
├── 04-quality/              ← 质量类
│   ├── 需求评审报告.md        ← 5 个致命项+6 个重要项
│   └── 风险登记册.md          ← 10 个风险项+等级+降级预案
├── 05-delivery/             ← 交付类（后续创建：部署文档/用户手册/演示说明）
└── 06-management/           ← 管理类
    ├── 项目技术准备方案.md     ← v4，技术栈+subagent 体系+工作流
    └── qa-pass机制说明.md     ← 双标记各自背书的 git 提交门禁
```

### 文档放置规则

| 文档类型 | 放在哪里 | 谁写 | 纳入 git |
|---|---|---|---|
| 需求文档 | `docs/01-requirements/` | PRD subagent 产出 → 主 Agent 确认提升 | ✅ |
| 架构/设计文档 | `docs/02-design/` | 架构师 subagent 产出 → 主 Agent 确认提升 | ✅ |
| 技术调研 | `docs/02-design/research/` | 主 Agent 整合 | ✅ |
| 开发规范 | `docs/03-development/` | 主 Agent | ✅ |
| 质量报告 | `docs/04-quality/` | 各 subagent 产出 → 主 Agent 确认提升 | ✅ |
| 交付文档 | `docs/05-delivery/` | 主 Agent（阶段交付时创建） | ✅ |
| 管理文档 | `docs/06-management/` | 主 Agent | ✅ |
| subagent 临时产出 | `_scratch/` | 各 subagent 直接写 | ❌ gitignore |
| 归档旧文档 | `_archive/` | 主 Agent | ❌ gitignore |
| 项目记忆 | `.workbuddy/memory/` | 系统/主 Agent | ❌ 非正式文档 |
| Hook 脚本 | `.codebuddy/hooks/` | 主 Agent | ✅ |
| QA 标记文件 | `.codebuddy/*.flag` | 各 subagent | ❌ gitignore |
| 环境变量 | `.env` | 主 Agent | ❌ gitignore |
| 环境变量模板 | `.env.example` | 主 Agent | ✅ |

### 文档提升流程
```
subagent 在 _scratch/ 产出初稿
    ↓
主 Agent 审阅，确认内容成熟
    ↓
cp 到 docs/ 对应目录（重命名去版本后缀）
    ↓
从 _scratch/ 删除（避免双源）
    ↓
git add + commit
```

---

## 四、开发规范与约定

### 代码规范
- 前端：TypeScript 全量注解，ESLint + Prettier，函数式组件 + Hooks
- 后端：Python 3.13+，类型注解必填，async 优先
- 详见 `docs/03-development/开发规范.md`

### Git 规范
- 分支：`main`（主）、`feature/xxx`（功能分支）
- 提交：Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:` 等）
- 提交门禁：Hook1 拦截 git commit，需 `code-review-pass.flag` + `test-pass.flag` 两个标记都是 PASS

### 权限铁律
- **业务源码（`src/`、`backend/`）修改权永远只在主 Agent**
- Subagent 发现问题写报告到 `_scratch/`，不碰业务代码
- 代码检查 subagent 可写：`_scratch/*.md` + `.codebuddy/code-review-pass.flag`
- 测试 subagent 可写：`*.test.*` + `_scratch/*.md` + `.codebuddy/test-pass.flag`

### 敏感信息
- API key、密码存 `.env`（gitignore 排除）
- `.env.example` 作为模板可提交
- 禁止在代码/文档/日志中硬编码 key 或密码
- 管理台列表显示 key 只显示后 4 位（`sk-****1234`）

---

## 五、Subagent 体系

### 6 个 Subagent + 主 Agent

| 角色 | 嵌入 Skill | 职责 | 权限 |
|---|---|---|---|
| 需求评审 | grill-me | 拷问需求，挖边界遗漏+轻量商业视角 | 只读 |
| PRD | prd-generator | 深化为开发级 PRD | 只读+写 _scratch/ |
| 架构师 | Phase0前端+后端架构 / Phase1+ api-design-principles | 技术栈选型+架构设计 | 只读+写 _scratch/ |
| 代码检查 | code-reviewer | 代码审查，通过后写 PASS 标记 | 只读 src/+写 _scratch/+写 flag |
| 测试 | webapp-testing | E2E 测试，通过后写 PASS 标记 | 只读 src/+写 tests/+写 flag |
| 版本管理 | pr-creator + writing-changelogs | git 提交+CHANGELOG | git 操作+写 CHANGELOG |

主 Agent：总调度 + 唯一业务编码者 + 调用 3 个开发 Skill（ui-ux-pro-max + vercel-react-best-practices + vercel-composition-patterns）

### Phase 0 工作流
```
0a 需求评审 [已完成] → 0c 架构评估 [已完成 v1+v2] → 0b PRD 深化 [已完成]
    ↓
0c' 架构细化 v2 [已完成] → 0d 项目搭建 [待执行] → 0e 功能开发 → 0f 代码检查 → 0g 测试 → 0h 版本 → 0i 交付
```

---

## 六、Hooks 机制

5 个 Hook 已配置在 `.workbuddy/settings.json`：

| Hook | 事件 | 作用 |
|---|---|---|
| Hook1 | PreToolUse(Bash) | git commit 拦截，检查 code-review-pass.flag + test-pass.flag |
| Hook2 | UserPromptSubmit | 需求变更关键词检测，注入上下文提醒调需求 subagent |
| Hook3 | SessionStart | 自动恢复上下文（读记忆/日志/scratch/CHANGELOG） |
| Hook4 | PreCompact | 压缩前写状态快照到 compact-snapshot.md |
| Hook5 | PostToolUse(Write\|Edit) | src/ 下代码文件 TypeScript/ESLint 轻检 |

QA 门禁设计：各 subagent 各自背书（代码检查 subagent 写 code-review-pass.flag，测试 subagent 写 test-pass.flag），不由主 agent 手动写标记。

---

## 七、当前状态与下一步

### 已完成
- ✅ Phase 0a 需求评审（5 个致命项）
- ✅ Phase 0c 架构评估 v1（技术栈+RAG决策+字幕方案）
- ✅ Phase 0b PRD 深化（11 章开发级 PRD）
- ✅ Phase 0c' 架构细化 v2（补 4 个盲点）
- ✅ 文档体系建立（docs/ 六类目录）
- ✅ Hooks 机制（5 个 hook + QA 门禁）
- ✅ GitHub 仓库连接

### 待确认
- 架构 v2 的 6 项待确认事项（Whisper 模型选型/ffmpeg 安装/队列上限/失败降级/JWT 有效期/PPT warning 位置）

### 下一步
- 确认 6 项后进入 **0d 项目搭建**（初始化前后端骨架）

---

## 八、关键文件速查

| 要找什么 | 去哪里 |
|---|---|
| 项目是什么 | 本文件（agent.md）第一章 |
| 用什么技术 | 本文件第二章 + `docs/02-design/architecture.md` |
| 需求规格 | `docs/01-requirements/prd.md` |
| 字幕怎么处理 | `docs/02-design/research/字幕技术调研.md` |
| 架构怎么设计 | `docs/02-design/architecture.md` + `architecture-v2.md` |
| 环境怎么配 | `docs/03-development/开发环境配置.md` |
| 代码规范 | `docs/03-development/开发规范.md` |
| 有什么风险 | `docs/04-quality/风险登记册.md` |
| 工作流程 | `docs/06-management/项目技术准备方案.md` |
| 文档放哪里 | 本文件第三章 |
