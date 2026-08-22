# Phase 0 产品需求文档（PRD）

> 项目：AI 助学 Demo
> 阶段：Phase 0（v4 已确认范围）
> 日期：2026-08-23
> 状态：开发级 PRD，待主 Agent 进入 0d 项目搭建
> 依据：项目技术准备方案 v4.0、architecture.md、需求评审报告、风险登记册 v2、范围变更说明、需求变更记录-001、灵感来源.txt
> 篇幅约束：本 PRD 只定义 Phase 0 "做什么"与"验收标准"，不重做技术选型（技术栈详见 architecture.md）

---

## 1. 项目概述

### 1.1 一句话定位

Phase 0 交付一个**可直接拿来用的最小可用版 AI 助学产品**：用户在本机播放自备课程视频，选中字幕右键向 AI 提问，AI 结合课件与逐字稿时间窗答疑，支持多轮追问；管理员通过本地管理台配置大模型、扫描素材、管理用户。

### 1.2 Phase 0 范围（v4 最终确认）

- 核心助学链路：播放视频 → 暂停 → 选中字幕 → 右键唤起 → AI 结合课件+逐字稿答疑 → 多轮追问
- 字幕交互模板：选中字幕 → 提示词框预填"用户看到了[字幕内容]，疑问是[输入框]"
- 管理台：配置大模型 API（OpenAI 兼容协议，默认阿里云百炼/通义千问）
- 可换素材（方案 B）：约定目录 `./materials/{course_id}/` + 管理台扫描，支持 vtt/srt + md/pdf/ppt
- 素材更新：替换文件 → 管理台重新扫描 → 刷新数据库缓存
- 用户系统（简化版）：登录 + 双角色 JWT（admin/user）+ 预置账号 + admin 重置密码
- PPT 课件支持：python-pptx 提取文本
- 完整播放器交互体验（参考 B 站/学习平台形态）

### 1.3 不做的事清单（明确推到 Phase 1+）

- 用户注册（Phase 0 预置账号，不开放注册）
- 忘记密码（降级为 admin 重置）
- RAG 语义检索（Phase 0 用时间窗截取 + 课件章节粗筛）
- langchain（Phase 2+ 才需要）
- 视频上架、栏目分类、观看历史、视频上传
- UGC 平台属性
- Docker 部署
- docx 课件（用户另存为 PDF 即可）
- Claude/Gemini 原生协议适配
- 讲师口吻 Few-shot 复刻、课程笔记生成、知识点总结

---

## 2. 用户角色定义

| 角色 | 用户名 | 密码 | 权限 |
|---|---|---|---|
| admin | `admin` | `123456` | 登录学习端 + 访问管理台 + 配置大模型 + 扫描素材 + 重置任意用户密码 |
| user | `user25` | `123456` | 登录学习端 + 播放课程 + AI 提问 + 管理自己的会话 |

**预置账号机制**：首次启动时后端自动 seed 上述两个账号（bcrypt 加密密码 hash 入库），用户首次登录后可自行修改密码；admin 可重置任意 user 密码为默认值 `123456`。

**角色路由**：登录后 JWT 携带 role 字段，前端按 role 决定是否展示"管理台"入口；后端 `/api/admin/*` 路由校验 role=admin，否则 403。

---

## 3. 用户故事列表

### 3.1 admin 故事（A1~A5）

**A1：配置大模型 API**
作为 admin，我希望在管理台配置大模型 API（baseUrl + apiKey + modelName），以便切换不同厂商模型。
- AC1：管理台"模型配置"页有新增/编辑/删除表单，字段含 name、base_url、api_key、model_name、is_default
- AC2：api_key 列表只显示 `sk-****1234`（后 4 位），编辑时不回显
- AC3：保存后无需重启，下一次 `/api/chat/stream` 请求自动用新配置
- AC4：可设置一条为默认（is_default=true），其他自动置 false

**A2：扫描素材库**
作为 admin，我希望点"扫描素材库"按钮扫描 `./materials/` 目录，以便接入新课程。
- AC1：点击扫描后，后端扫描 `./materials/*/`，按扩展名识别视频/字幕/课件
- AC2：srt 字幕自动转 vtt 存储；pdf/ppt 课件提取纯文本缓存到 `courseware_text_cached`
- AC3：扫描结果列表显示每个 course_id 的状态（ready/error）+ 错误原因
- AC4：非标格式（ass/ssa/B站CC JSON）被拒绝并提示转换工具

**A3：重新扫描单个课程（素材更新）**
作为 admin，我希望替换素材文件后点"重新扫描"刷新缓存，以便更新课件或字幕。
- AC1：素材列表每行有"重新扫描"按钮，调用 `POST /api/admin/materials/{course_id}/rescan`
- AC2：重新扫描覆盖 `courseware_text_cached`、`subtitle_path`、`video_path` 等字段
- AC3：若该课程有进行中的 AI 会话，前端提示用户"素材已更新，建议清空会话重试"
- AC4：扫描失败时状态置 error + error_message 字段记录原因

**A4：重置用户密码**
作为 admin，我希望在管理台重置任意 user 的密码为默认值，以便用户忘记密码时能恢复访问。
- AC1：管理台"用户管理"页列出所有用户（username + role + created_at）
- AC2：每行有"重置密码"按钮，调用 `POST /api/admin/users/{id}/reset-password`
- AC3：重置后密码为 `123456`，界面提示"已重置为默认密码，请通知用户登录后修改"
- AC4：不能重置自己的密码（防止 admin 锁死自己）

**A5：查看系统配置**
作为 admin，我希望在管理台看到当前模型配置列表和素材状态，以便运维排查。
- AC1：管理台首页展示默认模型名 + 素材总数 + ready/error 数量
- AC2：模型配置列表展示 name/base_url/model_name/is_default
- AC3：素材列表展示 course_id/状态/扫描时间/课件格式

### 3.2 user 故事（U1~U8）

**U1：登录系统**
作为 user，我希望用预置账号登录，以便访问学习端。
- AC1：未登录访问任意学习页跳转 `/login`
- AC2：输入 username + password，调 `POST /api/auth/login`，成功返回 JWT
- AC3：JWT 存 localStorage，后续请求带 `Authorization: Bearer <token>`
- AC4：登录失败（用户名/密码错误）显示明确错误文案

**U2：浏览课程列表**
作为 user，我希望看到已扫描的课程列表，以便选择学习内容。
- AC1：首页展示状态=ready 的课程卡片（course_id + 缩略图 + 课件格式）
- AC2：点击卡片进入播放页 `/course/{course_id}`
- AC3：状态=error 的课程不展示给 user（仅 admin 可见）

**U3：播放视频并查看字幕**
作为 user，我希望播放视频并看到自定义渲染的字幕，以便学习。
- AC1：ArtPlayer 加载 `GET /api/materials/{course_id}/video` 流式返回视频
- AC2：字幕层从 `GET /api/materials/{course_id}/subtitle` 加载 vtt，渲染为可选中 div
- AC3：字幕层 pointer-events: auto，user-select: text，允许鼠标选中文本
- AC4：播放/暂停/进度条/音量/全屏基础控件可用

**U4：选中字幕右键提问（主链路）**
作为 user，我希望选中字幕片段右键提问，以便 AI 结合上下文答疑。
- AC1：选中字幕文本 → 右键 → 菜单项"以此段字幕向 AI 提问"
- AC2：点击后视频自动暂停，AI 侧边栏打开，输入框预填"用户看到了[选中字幕]，疑问是[输入框]"
- AC3：用户输入问题 → 点发送 → 调 `POST /api/chat/stream`，SSE 流式渲染回答
- AC4：回答完成后可继续输入追问（多轮，最近 5 轮历史）

**U5：字幕选中降级（L2/L3）**
作为 user，当字幕选中失败时，我希望右键仍能提问，以便不中断学习。
- AC1（L2）：右键时 selection 为空 → 菜单项变为"以当前时间点字幕向 AI 提问"，自动取当前 cue 整条文本
- AC2（L3）：当前无 cue → 菜单项变为"以当前播放时间点向 AI 提问"，只传时间戳
- AC3：每级降级在 UI 提示当前模式（如"已使用整条字幕"）
- AC4：降级链路正常调用 `/api/chat/stream`，后端按时间戳取时间窗逐字稿

**U6：字幕层兜底（L4）**
作为 user，当字幕层渲染失败时，我希望手动插入字幕提问，以便极端情况仍可用。
- AC1：AI 输入框旁边有"插入当前字幕"按钮，点击插入当前 cue 文本
- AC2：按钮不可用时（无 cue）置灰
- AC3：用户可纯手动输入问题不依赖字幕
- AC4：L4 触发时界面提示"字幕交互降级为手动模式"

**U7：多轮追问与清空会话**
作为 user，我希望连续追问并在历史过长时清空会话，以便控制上下文。
- AC1：每次追问带上 session_id，后端自动累加历史
- AC2：对话面板显示"已保留最近 5 轮历史"
- AC3：超过 5 轮时提示"建议清空会话重新开始"
- AC4：点"清空会话"调 `POST /api/chat/sessions/{session_id}/clear`，清空后从第 1 轮重新开始

**U8：修改自己的密码**
作为 user，我希望修改自己的密码，以便安全使用。
- AC1：用户菜单有"修改密码"入口，弹窗含旧密码/新密码/确认新密码
- AC2：调 `POST /api/auth/change-password`，成功后提示需重新登录
- AC3：旧密码错误时返回明确错误
- AC4：新密码不少于 6 位

### 3.3 公共故事（C1~C2）

**C1：错误处理与降级文案**
作为任意角色，当 AI 请求失败时，我希望看到友好错误文案，以便知道如何处理。
- AC1：API key 失效 → "大模型 API Key 无效，请联系管理员检查配置"
- AC2：限流 → "请求过于频繁，请稍后再试"
- AC3：超时 → "请求超时，请检查网络后重试"
- AC4：余额不足 → "大模型账户余额不足，请联系管理员充值"

**C2：兼容性**
作为任意角色，我希望在主流浏览器使用，以便不用换浏览器。
- AC1：Chrome 120+ 完整支持所有功能（字幕选中主链路）
- AC2：Edge 120+ 完整支持
- AC3：Firefox 120+ 支持（字幕选中降级 L2 可用）
- AC4：Safari/iOS Safari 字幕选中可能不可用，UI 提示"建议使用 Chrome/Edge 获得最佳体验"

---

## 4. 核心交互流程图（文字版）

### 4.1 主链路（播放→暂停→选中→右键→提问→AI回答→追问）

```
1. user 登录→JWT→课程列表→选课程→播放页
2. ArtPlayer 加载视频 + 字幕层加载 vtt（自定义渲染 cue div）
3. user 播放，字幕层随 cue 更新
4. user 暂停 → 选中字幕片段（Selection API）→ 右键 → 取 window.getSelection().toString() → 弹菜单"以此段字幕向 AI 提问"
5. 点击菜单项 → art.pause() → AI 侧边栏打开 → 输入框预填"用户看到了[选中字幕]，疑问是[输入框]"
6. user 输入问题 → POST /api/chat/stream {course_id, selected_subtitle, start_time, end_time, user_question, session_id, model_config_id}
7. 后端 context_builder 构造 messages（系统prompt+课件粗筛+逐字稿时间窗±3min+历史5轮+用户问题）→ httpx POST {baseUrl}/v1/chat/completions stream=true → SSE chunk 逐个 yield
8. 前端 fetch+ReadableStream 逐字渲染 → 完成后可继续追问（带同一 session_id）
```

### 4.2 字幕选中降级链路（L1~L4）

```
L1 正常：selection 非空 → 菜单"以此段字幕向 AI 提问" → 传 selectedText
   ↓（selection 为空）
L2 降级：取当前 cue 整条文本 → 菜单"以当前时间点字幕向 AI 提问" → 传 cueFullText
   ↓（当前无 cue）
L3 降级：只传时间戳 → 菜单"以当前播放时间点向 AI 提问" → 后端用时间戳取时间窗逐字稿
   ↓（字幕层渲染失败）
L4 兜底：AI 输入框旁"插入当前字幕"按钮 + 纯手动输入
```

### 4.3 素材接入流程（方案 B）

```
1. admin 准备三件套：video.mp4 + subtitle.vtt(或.srt) + courseware.md(或.pdf/.ppt)
2. 放到 ./materials/{course_id}/（目录名=course_id，建议英文/拼音）
3. 管理台 → 素材管理 → 点"扫描素材库"
4. 后端扫描 ./materials/*/，按扩展名识别角色；srt→vtt 转换；pdf/ppt 提取文本缓存；入库 materials 表（status=ready/error）
5. 前端课程列表显示新入库素材
```

### 4.4 素材更新流程

```
1. admin 替换 ./materials/{course_id}/ 下文件
2. 管理台素材列表 → 该课程行 → 点"重新扫描" → POST /api/admin/materials/{course_id}/rescan
3. 后端重新识别 + 覆盖 courseware_text_cached + 更新 scanned_at
4. 若该课程有进行中 AI 会话 → 提示用户"素材已更新，建议清空会话重试"
```

### 4.5 管理台配置流程

```
1. admin 登录 → /admin（role=admin 才能访问）
2. 首页：默认模型名 + 素材统计 + 用户数
3. 模型配置页：新增/编辑/删除/设默认（api_key 编辑不回显，留空=不修改；保存即生效，cache 失效）
4. 素材管理页：扫描/重新扫描/查看错误
5. 用户管理页：列表 + 重置密码
```

### 4.6 登录与角色路由流程

```
1. 未登录访问 → 跳转 /login
2. username+password → POST /api/auth/login → 返回 JWT（含 user_id, username, role, exp）→ 存 localStorage
3. 前端读 JWT role：admin 显示"管理台"入口；user 仅学习端
4. 后端 /api/admin/* 校验 role=admin，否则 403
5. 登出 → POST /api/auth/logout（清 JWT + 跳 /login）；JWT 过期（1h）→ 401 → 跳 /login
```

---

## 5. 功能清单（按模块）

### 5.1 播放器模块
- ArtPlayer 集成（关闭原生 track，自定义字幕渲染层）
- 视频加载（`GET /api/materials/{course_id}/video` 流式）
- 播放/暂停/进度条/音量/全屏基础控件
- 字幕层 overlay div（pointer-events: auto, user-select: text）
- cue 更新监听 → 渲染当前 cue 为可选中 div（带 data-start/data-end）

### 5.2 字幕交互模块
- 字幕文本选中（浏览器原生 Selection API）
- 右键 contextmenu 监听 → 弹自定义菜单
- 右键菜单项"以此段字幕向 AI 提问"
- **降级链路 L1~L4**（详见第 8 章）
- "插入当前字幕"按钮（L4 兜底）

### 5.3 AI 对话模块
- AI 侧边栏（流式渲染 SSE）
- 提示词模板预填"用户看到了[字幕]，疑问是[输入框]"
- 多轮追问（最近 5 轮历史，超出自动丢弃最旧）
- 会话清空
- **Token 超限策略**（详见第 7 章）
- 错误降级文案（详见第 11 章）

### 5.4 管理台模块【显式回应 P0-2】
- 访问入口：`/admin`（绑定 127.0.0.1，禁止远程）
- 鉴权：JWT + role=admin 校验
- 模型配置 CRUD（api_key mask 显示 + 编辑不回显）
- 素材扫描（全量扫描 + 单课程重新扫描）
- 用户管理（列表 + 重置密码）
- 系统首页（统计概览）

### 5.5 素材管理模块【显式回应 P0-3】
- 方案 B：约定目录 `./materials/{course_id}/` + 管理台扫描
- 格式支持：视频 .mp4/.webm，字幕 .vtt/.srt，课件 .md/.pdf/.ppt
- srt → vtt 自动转换（入库时）
- pdf/ppt 文本提取缓存（pymupdf / python-pptx）
- 非标格式拒绝清单（ass/ssa/B站CC JSON）+ 提示转换
- 大小限制：视频<2GB，字幕<10MB，课件<50MB
- 素材更新流程（重新扫描覆盖缓存）

### 5.6 用户系统模块【覆盖架构盲点 1】
- 登录（username + password → JWT）
- 登出（清 JWT）
- 获取当前用户（GET /api/auth/me）
- 修改自己的密码
- admin 重置任意用户密码
- 预置账号 seed（admin/123456 + user25/123456）
- 角色路由（admin 可见管理台入口，user 不可见）

---

## 6. MVP 边界

### 6.1 Phase 0 做

| 模块 | 做 |
|---|---|
| 播放器 | ArtPlayer + 自定义字幕层 + 基础控件 + 右键菜单 |
| 字幕交互 | 选中→右键→提问（L1）+ 降级（L2/L3/L4）|
| AI 对话 | SSE 流式 + 多轮（5轮）+ 时间窗截取 + 课件粗筛 + Token 预检 |
| 管理台 | 模型配置 CRUD + 素材扫描 + 用户管理 + 系统首页 |
| 素材 | 方案 B + vtt/srt + md/pdf/ppt + 更新流程 |
| 用户系统 | 登录/登出/改密/重置 + 预置账号 + 双角色 JWT |
| 部署 | 本地开发模式（前后端同源代理，绑定 127.0.0.1）|

### 6.2 Phase 0 不做（避免开发期范围蔓延）

- 用户注册 / 忘记密码 / 邮件服务
- RAG / langchain / 向量库
- 视频上架 / 栏目分类 / 观看历史 / 视频上传
- docx 课件 / 扫描版 PDF OCR
- Claude/Gemini 原生协议
- Docker 部署 / 跨课程检索
- 讲师口吻复刻 / 笔记生成 / 知识点总结
- 多模型并行调用 / 重排序 / 引用标注

---

## 7. P0-4 Token 超限策略细化【显式回应 P0-4】

### 7.1 上下文构造策略

后端 `context_builder` 按以下顺序构造 messages：

1. **系统 prompt**（助学者模板，约 200 token）
2. **课件粗筛**：取选中字幕所在章节 + 前后各 1 章（共 3 章）；若选中字幕时间戳无法匹配章节，回退到课件全文（管理台 warning）
3. **逐字稿时间窗**：选中字幕 ±3 分钟（共 6 分钟窗口）
4. **多轮历史**：最近 5 轮（10 条 messages），超出自动丢弃最旧
5. **当前提问**：选中字幕片段 + 用户问题

### 7.2 Token 预检阈值（硬性参数）

后端在调用大模型前估算总 token 数，按阈值执行策略：

| 估算总 token | 策略 |
|---|---|
| ≤ 28K | 正常发送请求 |
| 28K ~ 30K | 截断历史到最近 3 轮（6 条 messages），重新估算后发送 |
| 30K ~ 32K | 截断历史到最近 1 轮（2 条 messages）+ 界面提示"上下文过长，已精简历史" |
| > 32K | 拒绝请求，返回错误"上下文超限，请清空会话或选择大上下文模型" |

### 7.3 课程上下文不累积

课件 + 逐字稿时间窗每轮重新计算（基于当前选中字幕时间戳），不随多轮历史累积。只有对话历史（user/assistant 消息）累加，且受 5 轮上限 + Token 预检双重约束。

### 7.4 兜底降级（嵌入风险 R-05 降级）

- 极端情况（课件无章节且 >2 万字）：截断到最近上下文 + 界面提示"课程过长，建议分段学习"
- 完全失败：返回友好错误，提示用户清空会话重试
- 管理台可选大上下文模型（128K）供长课程场景切换

---

## 8. P0-5 字幕格式容错细化【显式回应 P0-5】

### 8.1 支持的格式

| 格式 | 处理方式 |
|---|---|
| .vtt（标准 WebVTT）| 直接入库使用 |
| .vtt（带样式标签，含 `<c>`/`<i>`/`<b>`）| 入库时清洗掉样式标签，存纯文本 |
| .srt | 入库时自动转 vtt 存储（Python webvtt-py 或 pysrt）|

### 8.2 不支持的格式（拒绝清单）

| 格式 | 检测方式 | 拒绝文案 |
|---|---|---|
| .ass / .ssa | 首行 `Script Info:` 或含 `[Events]` | "暂不支持 ASS/SSA 字幕，请转换为 srt 格式" |
| B站 CC JSON | 首字符 `{` 且含 `body` 字段 | "暂不支持 B 站 CC JSON，请用字幕下载工具导出 srt/vtt" |

### 8.3 降级链路 L1~L4（可执行参数）

| 级别 | 触发条件 | 行为 | UI 提示 |
|---|---|---|---|
| **L1 正常** | `window.getSelection().toString()` 非空 | 菜单"以此段字幕向 AI 提问"，传 selectedText | 无 |
| **L2 降级** | 右键时 selection 为空 | 菜单"以当前时间点字幕向 AI 提问"，取当前 cue 整条文本（cueEl.textContent）| "已使用整条字幕" |
| **L3 降级** | 当前无 cue（视频暂停在无字幕时段）| 菜单"以当前播放时间点向 AI 提问"，只传 startTime，后端用时间戳取时间窗逐字稿 | "已使用当前时间点上下文" |
| **L4 兜底** | 字幕层渲染失败（cue 不更新）| AI 输入框旁"插入当前字幕"按钮 + 纯手动输入 | "字幕交互降级为手动模式" |

### 8.4 实现要点

- 右键菜单根据当前状态动态显示可用项（不可用项隐藏，不灰显，避免误操作）
- L2 取 cue 整条文本时，`cueFullText = cueEl?.textContent || ''`
- L3 时间戳取 `cueEl?.dataset.start` 或 `art.currentTime`
- L4 按钮点击时插入当前 cue 文本（若可用），否则空输入

---

## 9. 数据模型【覆盖架构盲点 1：含 users 表】

字段说明：`password_hash`=bcrypt，`api_key_encrypted`=AES-GCM（密钥从环境变量 `APP_SECRET` 读），`messages_json`=JSON 数组存多轮 messages。

```sql
users(id PK, username UNIQUE, password_hash, role CHECK IN('admin','user'), created_at, updated_at)
  -- 首次启动 seed: admin/bcrypt('123456'), user25/bcrypt('123456')

model_configs(id PK, name, base_url, api_key_encrypted, model_name, is_default BOOL, created_at, updated_at)

materials(id PK, course_id UNIQUE, dir_path, video_path, subtitle_path,
  subtitle_source_format,  -- 'vtt' 或 'srt'（原始格式）
  courseware_path, courseware_format,  -- 'md'/'pdf'/'ppt'
  courseware_text_cached,  -- 提取的纯文本缓存
  courseware_has_chapters BOOL,  -- 是否有章节结构（影响粗筛）
  status CHECK IN('ready','error'), error_message, scanned_at)

chat_sessions(id PK, session_id UNIQUE, user_id FK→users, course_id,
  selected_subtitle, selected_subtitle_start REAL, selected_subtitle_end REAL,
  messages_json, model_config_id FK→model_configs, created_at, updated_at)

system_settings(key PK, value)  -- JWT 密钥、默认模型 id、扫描路径等
```

---

## 10. API 接口清单

### 10.1 用户接口【覆盖架构盲点 1：含用户接口】

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| POST | `/api/auth/login` | 登录（username+password → JWT）| 无 |
| POST | `/api/auth/logout` | 登出（前端清 JWT，后端可选记录）| 需 JWT |
| GET | `/api/auth/me` | 获取当前用户信息（user_id, username, role）| 需 JWT |
| POST | `/api/auth/change-password` | 修改自己的密码（old_password, new_password）| 需 JWT |
| POST | `/api/admin/users/{id}/reset-password` | admin 重置任意用户密码为 `123456` | 需 admin |

### 10.2 素材接口

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/api/materials` | 课程列表（user 只看 ready，admin 看 all）| 需 JWT |
| GET | `/api/materials/{course_id}` | 单课程元数据 | 需 JWT |
| GET | `/api/materials/{course_id}/video` | 视频流（StreamingResponse，支持 Range）| 需 JWT |
| GET | `/api/materials/{course_id}/subtitle` | 字幕 vtt 文件 | 需 JWT |
| GET | `/api/materials/{course_id}/courseware-text` | 课件纯文本（从缓存读）| 需 JWT |

### 10.3 AI 对话接口

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| POST | `/api/chat/stream` | SSE 流式对话（body: course_id, selected_subtitle, start_time, end_time, user_question, session_id, model_config_id）| 需 JWT |
| POST | `/api/chat/sessions/{session_id}/clear` | 清空指定会话历史 | 需 JWT + 会话属主 |
| GET | `/api/chat/sessions` | 当前用户的会话列表 | 需 JWT |

### 10.4 管理台接口【显式回应 P0-2】

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/api/admin/model-configs` | 模型配置列表（api_key mask）| 需 admin |
| POST | `/api/admin/model-configs` | 新增配置 | 需 admin |
| PUT | `/api/admin/model-configs/{id}` | 编辑配置（api_key 留空=不修改）| 需 admin |
| DELETE | `/api/admin/model-configs/{id}` | 删除配置 | 需 admin |
| POST | `/api/admin/materials/scan` | 全量扫描 `./materials/` | 需 admin |
| POST | `/api/admin/materials/{course_id}/rescan` | 重新扫描单课程 | 需 admin |
| GET | `/api/admin/users` | 用户列表 | 需 admin |

### 10.5 管理台前端页面

- `/admin` 管理台首页（统计概览）
- `/admin/model-configs` 模型配置管理
- `/admin/materials` 素材管理（扫描/重新扫描/错误列表）
- `/admin/users` 用户管理（列表/重置密码）

---

## 11. 非功能需求

### 11.1 性能

- 视频首帧 < 2s（本地 500MB 内视频）；AI 首 token < 3s（后端不引入额外延迟）；字幕 cue 渲染 < 100ms；配置保存后下次请求即生效（cache 写时失效）

### 11.2 安全【嵌入风险 R-07 降级】

- 后端绑定 127.0.0.1（第一道防线）；JWT 1h 有效期；API key AES-GCM 加密（密钥从 `APP_SECRET` 环境变量读）；前端不直接调大模型 API；管理台 api_key mask 显示 `sk-****1234` + 编辑不回显；`.env` 加入 `.gitignore`；admin 不能重置自己

### 11.3 兼容性

- Chrome 120+ / Edge 120+：完整支持；Firefox 120+：字幕选中降级 L2 可用；Safari/iOS Safari：字幕选中可能不可用，UI 提示"建议使用 Chrome/Edge"

### 11.4 错误处理与降级文案【嵌入风险 R-05/R-07/R-08/R-09 降级】

| 场景 | 降级文案 |
|---|---|
| API key 失效 / 限流429 / 超时 / 余额不足 | "大模型 API Key 无效，请联系管理员" / "请求过于频繁，请稍后再试" / "请求超时，请检查网络" / "大模型账户余额不足，请联系管理员充值" |
| Token 超限 >32K / 30K~32K | "上下文超限，请清空会话或选择大上下文模型" / "上下文过长，已精简历史到最近 1 轮" |
| 字幕选中 L2 / L3 / L4 | "已使用整条字幕" / "已使用当前时间点上下文" / "字幕交互降级为手动模式" |
| 素材扫描失败 / 非标字幕格式 | 列表 status=error + error_message / "暂不支持 ASS/SSA，请转换为 srt 格式"等 |
| JWT 过期 | 跳转登录页 |

---

## 12. 待决议项与开放问题

### 12.1 PRD 完成后需回流到架构师补充的 3 项【覆盖架构盲点】

1. **用户系统对架构的影响**：architecture.md 原假设"管理台单密码鉴权"，PRD 已升级为双角色 JWT + users 表。架构师需补充：
   - JWT 中间件实现细节（FastAPI dependency）
   - users 表迁移与 seed 机制
   - role 校验在 `/api/admin/*` 的统一拦截点
   - 重置密码接口的安全审计日志

2. **PPT 课件支持对架构的影响**：architecture.md 原仅支持 md/pdf，PRD 新增 .ppt。架构师需补充：
   - python-pptx 依赖与文本提取策略（按页分割 + 标题识别）
   - PPT 无章节结构时的粗筛降级（回退全文 + warning）
   - `courseware_format` 字段取值 `ppt` 的处理分支

3. **素材更新流程对架构的影响**：architecture.md 方案 B 提及"重新扫描"但未细化。架构师需补充：
   - `rescan` 接口与 `scan` 接口的代码复用
   - 重新扫描时正在进行的 AI 会话如何处理（建议：不中断，但下次提问用新缓存）
   - 缓存失效策略（courseware_text_cached 覆盖写）

### 12.2 开发期可能需要再确认的问题

1. **默认大模型配置**：Phase 0 默认用哪家？（建议阿里通义，但取决于用户已有 API key）
2. **素材目录路径**：`./materials/` 是否合适？是否需要管理台可配置？
3. **方案 C 降级**：前端 input file 运行时加载是否要做？还是 Phase 0 只做方案 B？
4. **会话历史持久化**：chat_sessions 表是否必须落库？还是内存即可？（建议落库，便于刷新恢复）
5. **JWT 刷新机制**：1 小时过期后是否做 refresh token？还是直接重新登录？（建议直接重新登录，Phase 0 简化）
6. **PPT 文本提取质量**：python-pptx 提取的文本可能丢失排版信息，是否需要在管理台 warning？

---

## PRD 完成自检清单

- [x] 11 个章节齐全（1.项目概述 / 2.用户角色 / 3.用户故事 / 4.交互流程 / 5.功能清单 / 6.MVP 边界 / 7.P0-4 Token / 8.P0-5 字幕 / 9.数据模型 / 10.API 接口 / 11.非功能需求 / 12.待决议项）
- [x] 5 个致命项已显式回应：
  - P0-1 范围错位 → 第 1 章 + 第 6 章（MVP 边界明确）
  - P0-2 管理台 → 第 5.4 节 + 第 10.4 节
  - P0-3 素材承载 → 第 5.5 节 + 第 4.3 节（方案 B 流程）
  - P0-4 Token 超限 → 第 7 章（含 28K/30K/32K 阈值）
  - P0-5 字幕容错 → 第 8 章（含 L1~L4 降级）
- [x] 3 个架构盲点已覆盖：
  - 用户系统 → 第 2 章 + 第 5.6 节 + 第 9.1 节（users 表）+ 第 10.1 节（用户接口）+ 第 12.1 节（回流架构师）
  - PPT 课件 → 第 5.5 节 + 第 9.3 节（courseware_format）+ 第 12.1 节（回流架构师）
  - 素材更新 → 第 4.4 节 + 第 5.5 节 + 第 10.4 节（rescan 接口）+ 第 12.1 节（回流架构师）
- [x] 用户故事都含 AC 编号验收标准（A1~A5 / U1~U8 / C1~C2，每个含 AC1~AC4）
- [x] 数据模型含 users 表（第 9.1 节）
- [x] API 接口含用户接口（第 10.1 节：login/logout/me/change-password/reset-password）
- [x] MVP 边界明确（第 6 章：6.1 做 / 6.2 不做）
- [x] P0-4/P0-5 参数具体可执行：
  - P0-4：±3 分钟窗口、3 章粗筛、5 轮历史、阈值 28K/30K/32K
  - P0-5：vtt+srt 支持、srt→vtt 转换、ass/ssa/B站CC 拒绝清单、L1~L4 降级参数
