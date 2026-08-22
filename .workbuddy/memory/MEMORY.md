# 项目记忆 · 助学 demo

## 项目方向
- AI 助学产品，分阶段演进
- Phase 0（当前）：跑通 AI 助学核心功能（视频→暂停→选中字幕→右键→AI答疑 + 管理台配置大模型 + 可换素材）
- Phase 1+（后续）：完整学习平台（视频上架/栏目/历史/上传/用户系统）
- 交付对象：B 站评审 + 自用（两者都是）

## Subagent 体系（6 角色 + 主 Agent）
- 去掉 CEO，轻量商业视角并入 grill-me
- 需求评审（grill-me）/ PRD（prd-generator）/ 架构师（Phase0前端+后端架构，Phase1+ api-design-principles）/ 代码检查（code-reviewer）/ 测试（webapp-testing）/ 版本管理（pr-creator+writing-changelogs）
- 主 Agent 用 3 个开发 skill：ui-ux-pro-max + vercel-react-best-practices + vercel-composition-patterns
- 权限铁律：业务源码 src/ 仅主 Agent 可写

## 技术栈（架构师评估确认）
- 前端：React 18 + Vite + Ant Design 5 + Zustand + ArtPlayer（自定义字幕渲染层，不用原生 track）
- 后端：Python FastAPI + SQLite + SQLAlchemy（本机已有 Python 3.13）
- 大模型：OpenAI 兼容协议，后端代理（key 不暴露前端），建议 DeepSeek
- 部署：本地开发模式，后端绑定 127.0.0.1，不上 Docker
- 素材承载：方案B（后端约定目录 ./materials/ + 管理台扫描），方案C降级补充

## RAG/langchain 决策
- Phase 0 不上 RAG：用时间窗截取（选中字幕±3分钟逐字稿 + 课件按章节粗筛），工程量<1天覆盖90%场景
- Phase 1 上 RAG：Chroma + bge-m3
- Phase 2+ 上 langchain：需要 Agent/工具调用/多步推理时
- 关键洞察：RAG 必要性不由课程长度决定，由"是否需要跨内容检索/课件能否分章节"决定
- context_builder 函数设计要解耦，Phase 0→1 演进时内部检索逻辑可替换

## Hooks 机制（5 个）
- Hook1 PreToolUse(Bash): git commit 拦截，检查两个独立标记文件（code-review-pass.flag + test-pass.flag），各 subagent 各自背书
- Hook2 UserPromptSubmit: 需求变更关键词检测，注入上下文提醒调需求 subagent
- Hook3 SessionStart: 自动恢复上下文（读记忆/日志/scratch/CHANGELOG）
- Hook4 PreCompact: 压缩前写状态快照到 compact-snapshot.md
- Hook5 PostToolUse(Write|Edit): src/ 下代码文件 TypeScript/ESLint 轻检

## QA 门禁设计（用户改进版）
- 各 subagent 各自背书：代码检查 subagent 写 code-review-pass.flag，测试 subagent 写 test-pass.flag
- 不由主 agent 手动写标记（原方案已废弃）
- hook 精确告知缺哪个检查，不是笼统"质检未通过"

## 待用户确认（架构师 5 项）
1. 前端框架 React18+Vite（Vue 熟手可改）
2. 默认大模型（建议 DeepSeek）
3. 管理台密码策略（环境变量+首次随机 vs 固定密码）
4. 素材目录路径（./materials/ 是否合适）
5. 是否支持方案 C 降级（前端 input file）
