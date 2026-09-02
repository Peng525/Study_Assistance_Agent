# AI 助学项目：Codex 协作规则

## 项目事实来源

- 开始工作前先读 `agent.md`、`.workbuddy/memory/MEMORY.md`、最新一份 `.workbuddy/memory/YYYY-MM-DD.md`。
- 需求以 `docs/01-requirements/prd.md` 的最新确认项为准；实现状态以最新 memory、`docs/04-quality/第一次完整检查报告-v1.md` 和当前代码为准。
- 文档中出现冲突时，优先级为：用户当前指令 > 最新 memory/变更记录 > PRD 最新确认项 > 架构文档 > 原始提议书。
- 项目当前是已完成核心功能的 Phase 0 产品，不是尚未搭建的空项目。

## 技术与验证

- 后端：FastAPI + SQLAlchemy + SQLite，代码在 `backend/app/`，测试在 `backend/tests/`。
- 前端：React 18 + Vite + TypeScript + Ant Design + Zustand，代码在 `frontend/src/`。
- 日常验证只运行本次改动直接相关的 pytest/Vitest；后端基础检查为 `python -m compileall`，前端基础检查为 `tsc --noEmit`。
- 只有跨层契约同时修改时才运行两侧相关测试；Vite、TypeScript、依赖或入口配置变化时增加 `npm run build`。
- 后端完整 pytest、前端完整测试和构建只在用户明确要求“演示前完整验收”或切换生产 QA 时执行。
- 所有 QA 结果只追加到 `docs/04-quality/Demo测试记录.md`，包含实际时间、命令、结果、未覆盖项和人工验证清单。
- 当前后端端口为 8080，Vite `/api` 代理必须保持同步。
- 不读取、打印或提交 `.env` 中的密钥。

## 主 Agent 与 subagent 权限

- 业务源码的实现和修改只由主 Agent 完成：`backend/app/`、`frontend/src/`。
- 非平凡 bug 修复可按需调用项目自定义 subagent；可并行的只读检查优先并行，写入任务保持串行。
- `requirement_reviewer`：需求变更、范围变化或验收标准不清时使用。
- `prd_writer`：需求确认后，需要整理 PRD/验收标准时使用。
- `architect`：跨前后端、数据模型、鉴权、并发、性能或高风险设计变更时使用。
- `tester`：任何业务代码修改完成后先串行调用；只跑相关单元测试，可补最少必要测试，不得修改业务源码。维护统一测试记录和自己的 test flag。
- `code_reviewer`：tester 完成后再调用；只检查本次差异中的明显逻辑错误、异常/错误提示、敏感信息、注释和测试弱化，不修改业务源码。维护统一测试记录和自己的 review flag。
- `release_manager`：仅在用户明确要求提交、推送或发布时使用；不得绕过 QA 门禁，不得擅自 push。
- 小型、单点且行为明确的修复无需调用需求、PRD 或架构 subagent；代码审查与测试门禁仍需执行。
- 生产级 reviewer/tester 提示词保存在 `.codex/agents/production-backup/`；除非用户明确切换生产阶段，否则不得自动启用。

### Subagent 通用执行契约

- 每个 subagent 都必须先遵守本文件，再读取其角色提示词要求的事实源；用户当前指令始终优先。
- 先核对当前代码和 `git diff`；需求/架构角色再读取 memory、PRD 和质量报告。QA 角色只有无法理解本次行为时才扩展读取，避免为小改动生成生产级矩阵。
- 不把“最小改动”或“保持 Phase 0”当成唯一目标。既要保证当前交付最小、完整、可验证，也要指出 Phase 1+/2 已知路线需要保留的稳定契约、扩展点、数据归属和迁移入口；不得无证据提前引入复杂基础设施。
- 非平凡结论必须给出文件、模块、接口、数据表、命令结果或可复现步骤等证据。证据不足时明确写为假设或待确认项，不得用“无痛迁移”“以后再做”等未经证明的表述。
- 输出先给结论，再给范围与直接证据。QA 角色只维护统一 Demo 测试记录，不再分别创建 `_scratch/code-review.md`、`_scratch/test-report.md`、覆盖矩阵或发布级风险报告。
- 发现需求冲突、难逆决策、权限越界、密钥风险、无法验证的外部依赖或需要用户取舍时，停止扩大范围并明确交回主 Agent；不得擅自代替用户或主 Agent 作产品决定。
- 不读取、打印、复制或记录 `.env` 密钥及请求头。报告和日志只保留脱敏后的必要证据。
- Subagent 只在获准目录内写入。业务实现始终由主 Agent 完成；tester 只能修改测试、统一测试记录及自己的 flag；code_reviewer 只能修改统一测试记录及自己的 flag。

## Bug 修复工作流

1. 复现或用代码证据确认问题，不凭截图猜测根因。
2. 对照 PRD 与最新 memory 判断是 bug 还是需求变更。
3. 若属于需求变更，先让 `requirement_reviewer` 给出边界、优先级和验收标准；必要时再调用 `prd_writer`/`architect`。
4. 主 Agent 做最小、完整的业务代码修改，并补充相关测试。
5. 业务代码或测试变化后两个轻量 QA flag 自动置为 `PENDING`。先调用 tester 运行相关单元测试，再调用 code_reviewer 做轻量差异检查；二者必须对应同一当前业务/测试快照且都为 PASS 才允许提交。
6. 汇报改动、验证结果、剩余风险；未获用户明确要求时不提交、不推送。

### Demo 轻量门禁规则

- tester 目标耗时约 10 分钟，code_reviewer 目标耗时约 5 分钟；记录真实起止时间，不虚构工时。
- 相关测试失败、静态检查失败、明确代码缺陷、环境阻塞、命令未完成或结果不明确时必须保持 `PENDING`。
- 两个 flag 只保存 `status`、`snapshot_hash`、`checked_at`、`summary` 和 `report`；snapshot 只覆盖业务源码、测试、依赖、启动脚本和 QA 配置，不包含 docs、个人记录或素材；不再要求报告哈希、HEAD/tree、文件全集或结构化命令数组。
- 日常测试不自动扩大为全量回归、E2E、覆盖率、故障注入或真实云服务验证。
- 用户明确要求演示前完整验收时，才运行 `backend/venv/Scripts/python.exe -m pytest tests -q`、前端 `npm test` 和 `npm run build`。

## Git 与文件范围

- 保留用户已有改动，不覆盖、不回滚无关内容。
- 禁止使用 `git reset --hard`、`git checkout --` 等破坏性命令，除非用户明确要求。
- 提交采用 Conventional Commits。
- 仓库只提交源码、测试、README、必要配置和 agent/hook 配置；`.env`、数据库、素材、memory、`docs/`、`_scratch/`、QA flag 不提交。
- `git commit` 前必须同时满足：`.codebuddy/code-review-pass.flag` 与 `.codebuddy/test-pass.flag` 均为轻量 JSON，`status` 为 `PASS`，且 `snapshot_hash` 与当前业务代码/测试快照一致。
