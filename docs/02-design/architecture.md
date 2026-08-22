# Phase 0 架构评估报告

> 评估角色：前端 + 轻量后端架构师
> 评估对象：项目提议书 V1.2、灵感来源.txt、需求评审报告、风险登记册 v2
> 评估边界：Phase 0 范围（核心助学链路 + 管理台配置 + 可换素材 + 字幕选中）
> 日期：2026-08-23
> 开发环境：Windows 11 + 本机已有 Python 3.13.12
>
> **文档性质说明**：本文档以技术栈选型理由和方案设计描述为主。文档中出现的代码块为**参考实现**，供开发阶段直接参考使用，不属于架构设计本身。架构方案以文字描述为准。

---

## 〇、结论先行（TL;DR）

| 决策项 | 推荐 | 一句话理由 |
|---|---|---|
| 前端框架 | **React 18 + Vite**（不上 Next.js） | Phase 0 是 SPA，Vite 启动快；React 生态对 ArtPlayer、AI SDK、企业级扩展最稳；Phase 1+ 可平滑迁 Next.js |
| 播放器 | **ArtPlayer.js + 自定义字幕渲染层**（不用原生 `<track>`） | 字幕选中是核心风险 R-04，原生 track 跨浏览器选中行为不可控 |
| UI 库 | **Ant Design 5** | 中文社区企业级首选，管理台表单组件齐全 |
| 状态管理 | **Zustand** | 轻量、Phase 0 够用；不上 Redux（过重）不上 Context（多状态共享 re-render） |
| 后端框架 | **Python FastAPI** | 本机已有 Python 3.13；FastAPI 原生 async + SSE 流式转发；Phase 1+ RAG/embedding 都是 Python 生态，无缝衔接 |
| 数据库 | **SQLite + SQLAlchemy** | Phase 0 数据量小（配置/素材元数据/会话历史），零运维单文件；Phase 1+ 可无痛迁 PostgreSQL |
| 大模型协议 | **OpenAI 兼容协议** | 事实标准，覆盖 DeepSeek/通义/智谱/Moonshot/豆包/OpenAI |
| 部署形态 | **本地开发模式**（不上 Docker） | Phase 0 开发体验优先；前后端 dev server 同源代理 |
| Phase 0 上 RAG | **不上** | 时间窗截取工程量 <1 天，覆盖 <3 小时课程（90% 场景），RAG 工程量 3-5 人天，性价比不划算 |
| Phase 0 上 langchain | **不上** | Phase 0 链路极简（拼 prompt→调 API→流式返回），langchain 抽象层增加调试成本不增价值 |
| 素材承载 | **方案 B：后端约定目录 + 管理台扫描**（方案 C 降级） | 工程量小、后端职责清晰、支持持久化；方案 A 过度设计 |

---

## 一、技术栈推荐

### 1.1 前端

#### 1.1.1 框架选型：React 18 + Vite（明确推荐）

**为什么不是 Vue/Nuxt：**
- Vue/Nuxt 完全能做，技术能力无差异。但用户明确要求"企业级"，React 在企业级长期扩展、招人、生态深度上略胜一筹
- ArtPlayer 在 React 下集成示例最多（ArtPlayer 官方文档示例以 React 为主）
- 字幕选中的 Selection API、AI 流式渲染的 useSyncExternalStore 等 React 18 新特性正好适配
- **如果用户已经是 Vue 熟手，可以改用 Vue 3 + Vite**，本报告其余方案不受影响。但默认推荐 React

**为什么不是 Next.js：**
- Phase 0 是纯 SPA 单页应用，无 SEO 需求、无 SSR 需求、无服务端路由
- Next.js 的 App Router、Server Components、RSC 在 Phase 0 是负担（增加心智成本，调试链路长）
- Phase 1+ 升级路径清晰：React + Vite → Next.js（迁页面到 app router 即可，组件代码完全复用）
- **结论**：Phase 0 用 Vite，Phase 1+ 视需求迁 Next.js

**为什么用 Vite 而非 CRA：**
- CRA 已停止维护（2023 起）
- Vite 启动 <1s，HMR 极快，是 React 官方推荐的现代构建工具

#### 1.1.2 播放器：ArtPlayer.js + 自定义字幕渲染层

**核心决策**：关闭 ArtPlayer 原生 `<track>` 字幕，自己渲染字幕层。

**为什么不用原生 `<track>`：**
- 原生 `<track>` 渲染的 cue 在浏览器里是 Shadow DOM 或原生渲染层，浏览器 Selection API 无法可靠选中
  - Chrome：`::cue` 伪元素，selection 能拿到但范围不准
  - Firefox：cue 渲染在不同 layer，selection 拿不到
  - Safari/iOS：完全无法选中
- 原生 track 无法挂自定义 `contextmenu`（右键菜单），需要 hack
- 字幕样式（位置、字体）原生 track 控制力差，无法做"选中高亮"

**自定义渲染层实现思路**（详见第五章）：
1. ArtPlayer 配置关闭原生字幕显示
2. 自定义一个绝对定位的 overlay div 作为字幕层
3. 监听 ArtPlayer 的 cue 更新事件，把当前 cue 渲染成 `<div class="subtitle-cue" data-start data-end>文本</div>`
4. 用户用浏览器原生 Selection API 选中（自动支持）
5. 监听 `contextmenu` 拿 `window.getSelection().toString()`，弹右键菜单"以此段字幕向 AI 提问"

#### 1.1.3 UI 组件库：Ant Design 5

**推荐 Ant Design 5**，理由：
- 用户要求"企业级"，Ant Design 是中文社区企业级 UI 的事实标准
- 管理台需要大量表单组件（Input、Select、Form、Table、Upload、Modal、Tabs），antd 全部齐备
- 中文文档完善，组件对中文排版优化好
- 与 React 18 兼容好，CSS-in-JS 无样式冲突

**为什么不选 shadcn/ui**：
- 需要 Tailwind CSS，引入 Tailwind 增加构建链路复杂度
- 中文产品复杂表单（多字段联动、动态校验）antd 更顺手
- shadcn/ui 更适合纯英文/海外项目

**为什么不选 Material-UI**：
- 中文产品 Material Design 风格不贴合用户习惯
- 表单组件密度低，企业级管理后台不友好

#### 1.1.4 状态管理：Zustand

**推荐 Zustand**，理由：
- API 极简，无 boilerplate，无 Provider 包裹
- Phase 0 状态分三类：① 播放器状态（播放进度、当前字幕） ② AI 对话状态（流式响应、多轮历史） ③ 素材元数据（当前课程、字幕文本、课件文本）
- Zustand 的 selector 模式避免不必要的 re-render（核心：流式输出时不让播放器重新 render）
- 包体积小（<1KB）

**为什么不是 Redux/Redux Toolkit**：
- Phase 0 状态简单，Redux boilerplate 过重
- 异步流（thunk/saga）对 SSE 流式输出反而不如原生 fetch + Zustand setter 直接

**为什么不是 Context API**：
- 多状态共享时，任意一个状态变化会导致所有 Consumer re-render
- 流式输出每帧更新状态，Context 会导致整页 re-render，性能不可接受

**额外**：
- 非流式请求（拉素材列表、拉配置）用 React Query（@tanstack/react-query）管缓存与 loading
- 流式请求（大模型对话）用原生 `fetch` + `ReadableStream` 自己控制，不用 React Query（流式不适合）

### 1.2 后端

#### 1.2.1 框架选型：Python FastAPI（明确推荐）

**为什么不是 Node.js（Express/Fastify/Koa）**：
- 用户本机已有 Python 3.13.12 环境，零额外安装成本
- Node.js 也能做，但 Phase 1+ 要引入 RAG/embedding/向量检索，Python 生态（langchain、chromadb、sentence-transformers、pymupdf、pdfplumber）远比 Node.js 成熟
- FastAPI 原生 async + StreamingResponse，SSE 流式转发代码极简
- Python 处理文本（字幕解析、PDF 提取）库丰富
- **关键**：Phase 0 不上 RAG 但 Phase 1+ 要上，后端语言一旦选定很难换，选 Python 是面向未来

**为什么是 FastAPI 而非 Flask/Django**：
- FastAPI 原生 async，SSE 流式转发一行 StreamingResponse 搞定
- Flask 是同步框架，流式响应需要 generator + workaround，不优雅
- Django 过重，Phase 0 不需要 ORM/Admin/Auth 全家桶
- FastAPI 自动生成 OpenAPI 文档，前端联调方便

#### 1.2.2 数据库：SQLite + SQLAlchemy

**需要数据库**，理由：
- 管理台配置（多模型配置、热更新、并发读写）不能纯 JSON 文件（并发写会丢、查询不便）
- 素材元数据需要持久化（扫描后入库，避免每次重启重扫）
- 会话历史可选持久化（Phase 0 可不存，内存即可）
- **不用数据库 = 用 JSON 文件 = 并发写丢数据 + 无事务 + 查询不便**

**为什么是 SQLite 而非 PostgreSQL/MySQL**：
- Phase 0 数据量极小（<10 条配置、<10 个素材、<100 条会话）
- SQLite 零运维，单文件 `app.db`，开发期可手动删库重建
- SQLAlchemy ORM 兼容，Phase 1+ 升 PostgreSQL 只改连接字符串
- 不需要额外起数据库进程

**表结构（Phase 0 最小）**：
- `model_configs`：大模型配置（id, name, base_url, api_key_encrypted, model_name, is_default, created_at）
- `system_settings`：系统配置（key, value）——管理员密码 hash、JWT 密钥等
- `materials`：素材元数据（id, course_id, dir_path, video_path, subtitle_path, subtitle_format, courseware_path, courseware_format, courseware_text_cached, scanned_at）
- `chat_sessions`（可选）：会话历史（id, course_id, selected_subtitle, messages_json, created_at）

#### 1.2.3 大模型 API 代理方案：FastAPI SSE 反向代理

**核心原则**：前端永远不直接调大模型 API，所有请求经后端代理。

**为什么必须后端代理**：
1. **key 保护**（R-07）：API key 不能暴露给前端（前端 JS 任何字段都可见）
2. **prompt 构造集中**：系统 prompt + 课件 + 逐字稿时间窗 + 选中字幕 + 历史，由后端 `context_builder` 统一拼装，前端只传 `selected_subtitle` + `user_question` + `session_id`
3. **流式控制**：后端解析大模型 SSE 流，重新打包成对前端友好的 SSE 流（统一格式、错误兜底）
4. **多模型路由**：管理台配置多模型，后端按 `model_config_id` 路由到不同 baseUrl
5. **可观测性**：后端记录调用日志（耗时、token、错误），便于排查

**数据流**：
```
前端 POST /api/chat/stream
  body: { course_id, selected_subtitle, selected_subtitle_time, user_question, session_id, model_config_id }
后端:
  1. context_builder 构造 messages:
     - system: 助学者 prompt
     - system: 课件文本（按章节粗筛后）
     - system: 逐字稿时间窗（选中字幕±3分钟）
     - user: 选中字幕片段 + 用户问题
     - history: 最近5轮（从 session 取）
  2. 从 model_configs 读 baseUrl/apiKey/modelName
  3. httpx.AsyncClient POST {baseUrl}/v1/chat/completions stream=true
  4. 把上游 SSE chunk 逐个 yield 给 StreamingResponse
  5. 同时累积完整响应写回 chat_sessions
前端:
  fetch + ReadableStream 逐字渲染到对话面板
```

**OpenAI 兼容协议**：
- 后端只实现一套调用逻辑，请求体 `{model, messages, stream, temperature, max_tokens}`
- 兼容：OpenAI、DeepSeek、Moonshot、智谱 GLM、阿里通义、字节豆包、零一万物、百川
- Phase 0 不做 Claude 原生协议、Gemini 原生协议适配（用户用兼容协议即可，主流国产模型都支持）

### 1.3 部署形态

#### 1.3.1 Phase 0：本地开发模式（推荐）

**不用 Docker**，理由：
- Phase 0 只有前端 + 后端两个进程，Docker 增加构建/调试复杂度
- 开发期需要频繁改代码、debug、看日志，本地直接跑最快
- 用户在 Windows 开发，Docker Desktop 占资源且 WSL2 配置可能踩坑

**启动方式**：
- 后端：`uvicorn main:app --reload --port 8000 --host 127.0.0.1`
- 前端：`npm run dev`（Vite 默认 5173）
- 数据库：SQLite 文件 `./app.db`，无需起进程

**为什么后端绑定 127.0.0.1 而非 0.0.0.0**：
- R-07 管理台安全风险，禁止远程访问是第一道防线
- Phase 0 用户本机自用，不需要局域网访问

#### 1.3.2 前后端同源代理配置（Vite proxy）

**开发模式**：Vite 配置 `server.proxy`，前端 `/api/*` 和 `/admin/*` 转发到 `http://localhost:8000`：

```js
// vite.config.js
export default {
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/admin': { target: 'http://localhost:8000', changeOrigin: true },
      '/materials': { target: 'http://localhost:8000', changeOrigin: true }, // 视频/字幕文件
    }
  }
}
```

**生产模式**（Phase 0 也可能需要给 B 站演示）：
- 后端 FastAPI 直接 serve 前端构建产物（`app.mount("/", StaticFiles(directory="../frontend/dist", html=True))`）
- 单端口 8000 同源访问，无 CORS
- 启动：`uvicorn main:app --host 127.0.0.1 --port 8000`

#### 1.3.3 Phase 1+ 部署演进

Phase 1+ 上 Docker Compose（前端 nginx + 后端 FastAPI + SQLite 卷 + 未来向量库服务）。
Phase 0 不做。

---

## 二、RAG / langchain 决策（用户特别关心）

### 2.1 Token 测算（先算清楚再决定）

#### 2.1.1 中文 Token 估算口径

- 中文逐字稿：1 字 ≈ 0.6-1 token（实际分词器差异大，保守按 1.2 token/字 估算以留余量）
- 一段 1 小时中文授课视频：
  - 中文口播速度约 200-300 字/分钟
  - 1 小时 ≈ 1.2万-1.8万字
  - Token 估算 ≈ 1.5万-2.2万 tokens
- 一段 1.5 小时视频：≈ 2.2万-3.3万 tokens
- 一段 3 小时视频：≈ 4.5万-6.6万 tokens

> **注意**：需求评审报告里写"1 小时课程逐字稿 3-5 万字 ≈ 5-8 万 tokens"，这个估算偏高（中文授课口播达不到 500 字/分钟）。但即便按我的保守估算，长课程 + 课件 + 多轮历史也容易顶到 32K 上下文上限，所以 R-05 风险评级"高"是正确的，只是阈值不同。

#### 2.1.2 完整上下文 Token 构成

一次完整 AI 提问的 token 构成：
| 部分 | 估算 token |
|---|---|
| 系统 prompt（助学者模板） | 200 |
| 课件全文（典型技术课程 Markdown，1-2 万字） | 1.2万-2.4万 |
| 逐字稿全文（1 小时课程） | 1.5万-2.2万 |
| 选中字幕片段 | 100-300 |
| 用户问题 | 50-200 |
| 多轮历史（5 轮，每轮问+答各 500 token） | 5000 |
| 输出预留 | 2000-4000 |
| **合计（1 小时课程）** | **3.5万-5.4万** |

**结论**：
- 1 小时课程 + 32K 模型：必爆（3.5万 token > 32K）
- 1 小时课程 + 128K 模型：勉强够，但成本高
- 1.5 小时以上 + 任意模型：必须裁剪

#### 2.1.3 时间窗截取后的 Token 构成

时间窗截取策略：选中字幕时间戳 ±3 分钟逐字稿 + 课件按章节粗筛 + 历史限 5 轮。

| 部分 | 估算 token |
|---|---|
| 系统 prompt | 200 |
| 课件按章节粗筛（取选中字幕相关章节，通常 1-2 章，约课件 20-30%） | 3000-6000 |
| 逐字稿时间窗（前后各 3 分钟，共 6 分钟） | 1500-2500 |
| 选中字幕片段 | 100-300 |
| 用户问题 | 50-200 |
| 多轮历史 5 轮 | 5000 |
| 输出预留 | 2000-4000 |
| **合计** | **1.2万-1.8万** |

**结论**：时间窗截取后任意长度课程都能塞进 32K 模型，覆盖 Phase 0 90%+ 场景。

### 2.2 RAG 必要性分析 + 课程长度阈值

#### 2.2.1 时间窗截取能解决 Token 超限吗？

**能，但有边界**：

| 课程长度 | 时间窗截取（±3min）+ 课件粗筛 | 32K 模型可用？ |
|---|---|---|
| <1 小时 | 完全够 | ✅ |
| 1-1.5 小时 | 完全够 | ✅ |
| 1.5-3 小时 | 完全够（时间窗只取相关片段，与课程总长无关） | ✅ |
| 3-5 小时 | 完全够（同上） | ✅ |
| >5 小时 | 完全够 | ✅ |

**关键洞察**：时间窗截取的 token 消耗与**课程总长度无关**（只取决于时间窗宽度 + 课件相关章节）。理论上任意长度的单课程都能用时间窗截取解决 Token 超限。

#### 2.2.2 时间窗截取什么时候不够用？

时间窗截取不是银弹，以下场景不够：

1. **课件无法按章节粗筛**（R-10）：
   - 如果课件是扫描版 PDF 无书签、或 PPT 无标题结构，无法按选中字幕匹配章节 → 只能传课件全文 → 课件 >2 万字时超限
   - **Phase 0 缓解**：管理台扫描素材时强制要求课件有章节结构（Markdown 用 # 标题识别，PDF 用书签/标题字号识别），无结构的课件在管理台标记 warning，但允许使用（全量传入，长课程可能超限）

2. **用户提问跨章节**（"这个和前面第 3 章讲的有什么区别"）：
   - 时间窗截取只取了当前时间点上下文，跨章节对比性问题答不了
   - **Phase 0 缓解**：用户可手动在提问里补充"参考第 X 章内容"，或选"全量课件"开关（管理台或对话面板提供，长课程会超限）
   - 这是 Phase 0 可接受局限

3. **多课程检索**（"我之前看过的另一门课讲过类似内容"）：
   - 跨课程检索必须 RAG，时间窗截取只能管单课程
   - **Phase 0 不支持**（用户自用场景以单课程为主），Phase 1+ RAG 解决

#### 2.2.3 明确课程长度阈值

**时间窗截取方案下的课程长度阈值**：

| 课程长度 | 时间窗截取是否可用 | 备注 |
|---|---|---|
| <3 小时 | ✅ 完全可用 | Phase 0 主战场 |
| 3-6 小时 | ✅ 可用 | 时间窗与总长无关 |
| >6 小时 | ⚠️ 可用但建议拆分 | 单视频过长 UX 差，建议用户拆分成多 P |

**RAG 真正必要的阈值**（不是基于课程长度，而是基于以下条件）：
- 跨课程检索（Phase 1+）
- 课件无章节结构且 >2 万字（Phase 0 罕见，管理台 warning 提示）
- 多轮追问累积超限且无法用历史裁剪解决（Phase 0 用 5 轮限制解决，不上 RAG）

> **关键判断**：课程长度不是上 RAG 的决定因素。**是否需要跨内容检索 / 是否课件无法分章节**才是。Phase 0 单课程 + 课件有结构，所以不上 RAG。

### 2.3 Phase 0 是否上 RAG：**不上**（明确建议）

#### 2.3.1 理由

1. **时间窗截取工程量 <1 天**，覆盖 Phase 0 90% 场景，性价比碾压 RAG
2. **RAG 工程量 3-5 人天**（向量库选型 + embedding 服务接入 + 分片策略 + 检索调优 + 评测），Phase 0 周期消化不了
3. **RAG 引入新故障面**：向量库服务可用性、embedding 模型可用性、检索质量波动、向量维度不匹配等
4. **RAG 收益在 Phase 0 不显著**：单课程场景，时间窗截取的精度（时间戳定位 ±3 分钟）通常比向量检索（语义近似）更高，因为视频学习就是按时间线性推进的
5. **需求评审报告明确建议**："Phase 0 必须有一个最小上下文裁剪策略，不能等 RAG。最简方案：按选中字幕时间戳取前后 N 分钟逐字稿片段 + 课件全文（或课件按章节标题粗筛）。这是时间窗截取，工程量很小。"
6. **提议书原文也明确 Phase 0 不上 RAG**：范围错位的是"Token 超限可接受"，不是"必须上 RAG"。架构方案：Phase 0 用时间窗截取替代 RAG 解决 Token 超限。

#### 2.3.2 Phase 0 替代方案（时间窗截取详细设计）

**`context_builder.py` 伪代码**：

```python
def build_messages(course, selected_subtitle, user_question, history):
    # 1. 课件粗筛：按选中字幕时间戳匹配最近章节
    courseware_text = filter_courseware_by_time(
        course.courseware_text,
        course.chapter_timestamps,  # 从字幕标题/课件标题提取
        selected_subtitle.start_time
    )
    # 如果课件无法分章节，回退到课件全文（管理台 warning）
    if not courseware_text:
        courseware_text = course.courseware_text  # 全量

    # 2. 逐字稿时间窗：选中字幕 ±3 分钟
    transcript_window = extract_transcript_window(
        course.transcript_cues,
        center_time=selected_subtitle.start_time,
        window_minutes=3
    )

    # 3. 多轮历史：最近 5 轮
    recent_history = history[-5:] if len(history) > 5 else history

    # 4. 拼 messages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"课件内容：\n{courseware_text}"},
        {"role": "system", "content": f"逐字稿（当前知识点前后片段）：\n{transcript_window}"},
        *recent_history,
        {"role": "user", "content": f"选中字幕：{selected_subtitle.text}\n\n我的问题：{user_question}"},
    ]
    return messages
```

**关键设计点**：
- `context_builder` 是独立函数，输入是结构化数据，输出是 messages
- Phase 1+ 上 RAG 时，只需把 `filter_courseware_by_time` 和 `extract_transcript_window` 替换为向量检索召回，函数签名不变，调用方无感知
- **这是 Phase 0→Phase 1 演进的核心解耦点**

### 2.4 RAG 工程量评估（Phase 1 上时用）

**如果 Phase 1 上 RAG，需要：**

| 组件 | 选型 | 理由 |
|---|---|---|
| 向量库 | **Chroma**（嵌入式，Python 原生） | Phase 1 单机部署，零运维；不选 Pinecone（云服务、Phase 1 本地开发不友好）、不选 Milvus（重） |
| 备选向量库 | **LanceDB** | 如果未来要持久化大量向量，LanceDB 性能更好 |
| embedding 模型 | **bge-m3**（开源，中英文，本地可跑）或云端 embedding API（OpenAI text-embedding-3-small / 阿里通义/智谱） | 自部署 bge-m3 零成本，但需要 GPU 或 CPU 推理资源；云端 API 简单但有成本 |
| 分片策略 - 逐字稿 | 按时间窗分片（每片 2-3 分钟，重叠 30 秒），元数据带时间戳 | 时间窗分片天然适配视频学习场景 |
| 分片策略 - 课件 | 按章节/标题分片（Markdown 按 # 分，PDF 按章节分），每片 500-1000 字 | 标题语义边界清晰 |
| 检索策略 | ① 选中字幕时间戳优先取时间窗内逐字稿 ② 选中字幕文本做语义检索召回课件相关章节 ③ 时间戳 + 语义双路召回融合 | 时间戳保精度，语义保覆盖 |
| 工程量 | **3-5 人天**（含调试） | 含向量库集成、embedding 接入、分片脚本、检索调优、评测集 |

### 2.5 langchain 必要性：**Phase 0 不需要**（明确建议）

#### 2.5.1 理由

1. **Phase 0 链路极简**：拼 prompt → 调 API → 流式返回，核心代码 <50 行
2. **langchain 抽象层增加调试成本**：流式输出在 langchain 里反而难控制（langchain 的 callback 机制对 SSE 流的中断、重连处理不直观）
3. **langchain 的价值在 Agent / 工具调用 / 多步推理**，Phase 0 是纯问答不需要
4. **对话历史自己管**（一个 list，限制 5 轮）比 langchain Memory 更可控、更易调试
5. **直接用 `httpx` 或 `openai` Python SDK 调 API**，调试链路最短，错误定位快
6. **langchain 版本迭代快、API 不稳定**，Phase 0 引入等于背上技术债

#### 2.5.2 何时引入 langchain

**Phase 2+** 在以下场景引入：
- 需要 Agent 能力（如查课程进度、生成笔记、跨课程检索等工具调用）
- 需要多步推理（如先检索知识点 → 再生成讲解 → 再做知识图谱）
- 需要复杂链路编排（如多模型路由 + 重排序 + 引用标注）

**Phase 1 引入 RAG 时不需要 langchain**：Chroma + bge-m3 直接调即可，不需要 langchain 的 Retriever 抽象。

### 2.6 演进路径：**可行**（明确判断）

```
Phase 0（当前）
├─ 时间窗截取（context_builder 函数）
├─ 直调 OpenAI 兼容 API（httpx）
├─ 自管对话历史（list，限 5 轮）
├─ 不上 RAG
└─ 不上 langchain

        │
        │ 替换 context_builder 内部的两个函数
        │ filter_courseware_by_time → 向量检索召回
        │ extract_transcript_window → 向量检索召回（保留时间窗兜底）
        ▼

Phase 1
├─ 引入 RAG（Chroma + bge-m3 或云端 embedding）
├─ context_builder 函数签名不变，调用方无感知
├─ 仍直调 OpenAI 兼容 API
├─ 仍自管对话历史
└─ 不上 langchain

        │
        │ 当需要 Agent / 工具调用 / 多步推理时
        ▼

Phase 2+
├─ 引入 langchain（如需 Agent 编排）
├─ RAG 已成熟
├─ 多模型路由 / 重排序 / 引用标注
└─ 完整学习平台
```

**关键判断**：这条路径可行，**前提是 Phase 0 的 `context_builder` 设计要解耦**（输入结构化数据，输出 messages，内部检索逻辑可替换）。本报告已在 2.3.2 给出该函数的伪代码签名。

---

## 三、P0-2 管理台最小设计

### 3.1 访问入口

| 项 | 值 |
|---|---|
| URL | `http://localhost:8000/admin`（开发模式同源经 Vite 代理；生产模式后端直接 serve） |
| 端口 | 8000（与后端 API 同源，避免 CORS） |
| 启动方式 | 与后端同进程（FastAPI 同时 serve API + 管理台静态文件） |
| 远程访问 | **禁止**（绑定 127.0.0.1） |

### 3.2 配置存储位置：SQLite（不解释，详见 1.2.2）

### 3.3 鉴权方式（最小方案）

**单密码 + localhost 绑定 + JWT**，三层防护：

1. **localhost 绑定**（第一道防线）：
   - `uvicorn main:app --host 127.0.0.1`
   - 局域网/外网完全无法访问管理台
   - 这是 Phase 0 最核心的安全策略

2. **单密码登录**（第二道防线）：
   - 启动时读环境变量 `ADMIN_PASSWORD`（用户在 `.env` 配置）
   - 首次启动未配置密码时，后端随机生成 16 位密码，打印到 stderr 让用户记录
   - 密码 hash（bcrypt）存 SQLite `system_settings` 表，不存明文
   - 登录页：单密码输入框，无用户名（Phase 0 无用户系统）

3. **JWT 短期令牌**（第三道防线）：
   - 登录成功后发 JWT（有效期 1 小时，密钥从环境变量读）
   - JWT 存 localStorage，每次请求带 `Authorization: Bearer <token>`
   - 过期后重新登录

**为什么不做 OTP / 双因素 / SSO**：
- Phase 0 自用，单密码 + localhost 绑定足够
- 多因素增加使用成本，B 站评审场景不需要

### 3.4 key 保护策略

**API key 全程不暴露给前端**：

| 环节 | 措施 |
|---|---|
| 存储 | SQLite `model_configs.api_key_encrypted` 字段，AES-GCM 加密（密钥从环境变量 `APP_SECRET` 读） |
| 读取 | 仅后端 `chat/stream` 路由内部解密使用，永不返回前端 |
| 管理台显示 | 列表只显示 `sk-****1234`（后 4 位），不返回完整 key |
| 管理台编辑 | 编辑时不回显 key（输入框留空表示不修改，填了才覆盖） |
| 启动日志 | 严禁打印 key（用 `***` mask） |
| 请求日志 | 调用大模型时日志里 key 用 `***` 替换 |
| 前端请求 | 前端调大模型必经后端 `/api/chat/stream`，前端代码里搜索不到任何 key |

**`APP_SECRET`（AES 密钥）来源**：
- 环境变量 `APP_SECRET`（用户在 `.env` 配置，32 字节随机字符串）
- 首次启动未配置时，后端随机生成并写入 `.env` 文件（提示用户妥善保管）
- `.env` 加入 `.gitignore`，禁止提交版本库

### 3.5 配置热更新

**改配置后不需要重启**：

- 管理台改配置 → 写 SQLite → 失效内存 cache
- 后端 `model_configs` 读取走 cache（dict），写入时清空 cache
- 下次 `/api/chat/stream` 请求自动读最新配置
- 实现：FastAPI 依赖注入 + 简单的 dict cache，写入时 `cache.clear()`

**为什么不直接每次查 DB**：
- 配置访问频率低（管理台改配置），但每次 AI 请求都要读一次（取 model 配置）
- cache + 写时失效 = 读快写一致，最优

### 3.6 支持的大模型协议范围

**Phase 0 仅支持 OpenAI 兼容协议**（一套表单字段：baseUrl + apiKey + modelName）：

| 厂商 | baseUrl | 兼容性 |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | 原生 |
| DeepSeek | `https://api.deepseek.com/v1` | 完全兼容 |
| Moonshot（Kimi） | `https://api.moonshot.cn/v1` | 完全兼容 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | 兼容（messages 格式一致） |
| 阿里通义 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 兼容 |
| 字节豆包 | `https://ark.cn-beijing.volces.com/api/v3` | 兼容 |
| 零一万物 | `https://api.lingyiwanwu.com/v1` | 完全兼容 |
| 百川 | `https://api.baichuan-ai.com/v1` | 兼容 |

**Phase 0 不做**：
- Claude 原生协议（Anthropic 自有 messages 格式，与 OpenAI 不兼容；用户用兼容代理或换模型即可）
- Gemini 原生协议（同上）
- 国产模型非标准接口（如讯飞星火自有协议）

**Phase 1+ 演进**：管理台支持配置"协议类型"字段（OpenAI兼容 / Claude / Gemini），后端按协议路由不同 adapter。

---

## 四、P0-3 可换素材承载方式

### 4.1 三方案评估

| 方案 | 优点 | 缺点 | 工程量 | 评分 |
|---|---|---|---|---|
| **A：管理台上传 + 后端存储** | 用户体验最好，可视化上传 | 后端要实现文件存储+元数据管理+大文件分片上传；Phase 0 过度设计 | 大（3-5 人天） | ❌ 不推荐 |
| **B：后端约定目录 + 管理台扫描** | 工程量小；后端读文件简单；支持持久化；用户操作直观（放文件 + 点扫描） | 用户需手动管理目录结构 | 小（1 人天） | ✅ **推荐** |
| **C：前端 input file 运行时加载** | 零后端存储；零部署 | 不支持持久化（刷新丢失）；大视频加载慢；PDF 前端解析复杂 | 中（2 人天，前端工作量大） | ⚠️ 降级方案 |

**推荐方案 B 为主，方案 C 作为降级/补充**：
- Phase 0 主路径走方案 B（用户放文件 → 管理台扫描 → 入库）
- 方案 C 作为"快速试用"补充（前端"快速加载"按钮，不持久化，适合 demo 演示）
- Phase 1+ 视需要补方案 A（管理台上传）

### 4.2 推荐方案接入流程

**用户接入素材流程**（方案 B）：

1. **用户准备素材三件套**：
   - 视频：`my-course.mp4`（任意文件名，扩展名 .mp4 或 .webm）
   - 字幕：`subtitle.vtt` 或 `subtitle.srt`
   - 课件：`courseware.md` 或 `courseware.pdf`

2. **用户在项目目录下创建课程目录**：
   ```
   ./materials/
     └── my-course/              ← 目录名 = course_id（建议英文/拼音，避免中文路径问题）
       ├── video.mp4
       ├── subtitle.vtt
       └── courseware.md
   ```

3. **用户打开管理台 → 素材管理页 → 点"扫描素材库"按钮**：
   - 后端扫描 `./materials/*/`
   - 识别每个目录下的视频/字幕/课件文件（按扩展名识别角色）
   - srt 自动转 vtt 存储到 `./materials/my-course/subtitle.vtt`（覆盖原 srt 或并存）
   - PDF 提取纯文本缓存到 SQLite `materials.courseware_text_cached`
   - 入库到 `materials` 表，状态 `ready`
   - 扫描失败（缺文件、格式不支持）状态 `error` + 错误原因

4. **前端课程列表显示已入库素材**，点击进入播放页

5. **更新素材**：用户替换目录下文件 → 管理台点"重新扫描"该课程 → 元数据与缓存刷新

### 4.3 格式约束清单

| 类型 | 支持格式 | 不支持 | 大小限制 | 备注 |
|---|---|---|---|---|
| 视频 | `.mp4`（H.264 + AAC）、`.webm` | avi、mkv、mov、flv | <500MB（开发期）；硬限制 <2GB | 浏览器原生支持，无需转码 |
| 字幕 | `.vtt`、`.srt` | ass、ssa、B站CC json | <10MB | srt 入库时自动转 vtt |
| 课件 | `.md`、`.pdf`（纯文本 PDF） | docx、pptx、扫描版 PDF | <50MB | PDF 用 pymupdf 提取文本；扫描版 PDF 提取出空文本，管理台 warning |

**为什么 docx/pptx 不支持**：
- Phase 0 文档转换工程量大（docx 解析需 python-docx，pptx 需 python-pptx，且 PPT 排版信息丢失后课件质量差）
- 用户用 Word/PPT 的"另存为 PDF"即可，零成本
- Phase 1+ 视需要补 docx/pptx 支持

**为什么扫描版 PDF 不支持**：
- 扫描版 PDF 是图片，pymupdf 提取出来是空文本
- OCR（如 paddleocr）工程量大，Phase 0 不做
- 管理台 warning 提示用户"课件文本为空，请提供可复制文本的 PDF"

### 4.4 三件套关联方式

**目录名 = course_id，目录内文件按扩展名识别角色**：

- 一个目录 = 一套完整素材（一个视频 + 一个字幕 + 一个课件）
- 一个目录只能一套素材（Phase 0 不支持多视频/多字幕/多课件）
- 文件名不强制要求（按扩展名识别角色）
- 如果目录内有多份同类型文件（如两个 .vtt），扫描时取第一份 + warning

**关联关系**：
- `materials.course_id` = 目录名
- `materials.video_path` = `./materials/{course_id}/video.mp4`（实际路径）
- `materials.subtitle_path` = `./materials/{course_id}/subtitle.vtt`
- `materials.courseware_path` = `./materials/{course_id}/courseware.md`
- 三者通过 `course_id` 关联，前端通过 `/api/materials/{course_id}` 拉取元数据

### 4.5 大小限制

| 类型 | 软限制（warning） | 硬限制（reject） |
|---|---|---|
| 视频 | >500MB 提示"开发期建议小视频" | >2GB 拒绝扫描 |
| 字幕 | >5MB 提示"字幕过长可能影响性能" | >10MB 拒绝 |
| 课件 | >30MB 提示"课件过大，PDF 文本提取可能慢" | >50MB 拒绝 |

---

## 五、字幕选中技术路线（核心风险 R-04）

### 5.1 路线选择：ArtPlayer + 自定义渲染层（明确推荐）

**不用原生 `<track>`**，理由：

| 浏览器 | 原生 `<track>` 选中行为 |
|---|---|
| Chrome | `::cue` 伪元素，selection 能拿到但范围不准；右键 contextmenu 不触发 cue 元素 |
| Firefox | cue 在独立渲染层，selection 拿不到 |
| Safari | 无法选中 |
| iOS Safari | 完全无法选中（且会触发原生文本选择 callout） |

**自定义渲染层**：把每条 cue 渲染成可见 `<div>`，用浏览器原生 Selection API 直接选中。

### 5.2 自定义渲染层实现思路

```typescript
// 1. ArtPlayer 配置关闭原生字幕
const art = new Artplayer({
  url: videoUrl,
  subtitle: { url: subtitleUrl, type: 'vtt', show: false }, // 关掉原生显示
  // ...
})

// 2. 自定义字幕 overlay div
const subtitleOverlay = document.createElement('div')
subtitleOverlay.className = 'subtitle-overlay'
// 绝对定位在视频底部，pointer-events: auto（允许选中）
videoContainer.appendChild(subtitleOverlay)

// 3. 监听 cue 更新，渲染当前 cue
art.on('subtitle_update', (cue) => {
  if (!cue) {
    subtitleOverlay.innerHTML = ''
    return
  }
  // 渲染成可选中 div，保留时间戳元数据
  subtitleOverlay.innerHTML = `
    <div class="subtitle-cue"
         data-start="${cue.startTime}"
         data-end="${cue.endTime}">
      ${escapeHtml(cue.text)}
    </div>
  `
})

// 4. 监听右键菜单
subtitleOverlay.addEventListener('contextmenu', (e) => {
  e.preventDefault()
  const selection = window.getSelection()
  const selectedText = selection.toString().trim()
  const cueEl = subtitleOverlay.querySelector('.subtitle-cue')
  const cueFullText = cueEl?.textContent || ''
  const cueStart = cueEl?.dataset.start || '0'
  const cueEnd = cueEl?.dataset.end || '0'

  // 弹右键菜单
  showContextMenu(e.clientX, e.clientY, {
    selectedText,           // 用户选中的片段
    cueFullText,            // 整条 cue 文本
    startTime: cueStart,    // cue 起始时间（秒）
    endTime: cueEnd,        // cue 结束时间（秒）
  })
})

// 5. 右键菜单项
contextMenuItems = [
  {
    label: '以此段字幕向 AI 提问',
    onClick: ({ selectedText, cueFullText, startTime }) => {
      // 暂停视频
      art.pause()
      // 触发 AI 提问，把字幕+时间戳带到 AI 输入框
      aiStore.openQuestion({
        selectedSubtitle: selectedText || cueFullText,  // 选中为空时降级取整条
        startTime,
        endTime,
      })
    }
  }
]
```

**CSS 要点**：
```css
.subtitle-overlay {
  position: absolute;
  bottom: 60px;  /* 避开 ArtPlayer 控件栏 */
  left: 0; right: 0;
  text-align: center;
  pointer-events: auto;  /* 关键：允许鼠标交互 */
  user-select: text;      /* 允许文本选中 */
}
.subtitle-cue {
  display: inline-block;
  background: rgba(0,0,0,0.7);
  color: white;
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 18px;
  line-height: 1.4;
  cursor: text;
}
```

### 5.3 srt → vtt 转换方案

**后端入库时统一转 vtt 存储**（不在前端转），理由：
- 后端转换一次缓存，避免每次播放重复转换
- 前端无需引入 srt 解析库
- 转换逻辑简单，Python 手写 20 行够用

**Python 转换实现**：

```python
import re

def srt_to_vtt(srt_content: str) -> str:
    """srt 字幕内容转 vtt 格式"""
    # 1. 首行加 WEBVTT
    # 2. 时间戳逗号改点（00:00:01,000 → 00:00:01.000）
    # 3. 去掉 srt 序号行
    lines = srt_content.strip().split('\n')
    output = ['WEBVTT', '']
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 跳过序号行（纯数字）
        if line.isdigit():
            i += 1
            continue
        # 时间戳行：把 , 改成 .
        if '-->' in line:
            line = line.replace(',', '.')
            output.append(line)
            i += 1
            # 后续字幕文本行（直到空行）
            while i < len(lines) and lines[i].strip():
                output.append(lines[i])
                i += 1
            output.append('')  # 空行分隔 cue
        else:
            i += 1
    return '\n'.join(output)
```

**Phase 0 用现成库**：`webvtt-py`（`pip install webvtt-py`）或 `pysrt`，更稳健。手写版本作为兜底。

### 5.4 字幕选中失败的降级方案

**三级降级**：

| 级别 | 触发条件 | 方案 |
|---|---|---|
| L1 正常 | 用户能选中字幕片段 | 选中文本 → 右键 → AI 提问 |
| L2 降级 | 用户右键时 selection 为空（没选中或选中失败） | 右键菜单变为"以当前时间点字幕向 AI 提问"，自动取当前 cue 整条文本 |
| L3 降级 | 当前无 cue（视频暂停在无字幕时段） | 右键菜单变为"以当前播放时间点向 AI 提问"，只传时间戳不传字幕文本，后端用时间戳取时间窗逐字稿 |
| L4 兜底 | 极端情况（字幕层渲染失败） | AI 输入框旁边放"插入当前字幕"按钮，用户手动点；或纯手动输入问题 |

**实现要点**：
- 右键菜单根据当前状态动态显示可用项（不可用项灰显或隐藏）
- 每级降级都在 UI 上明确提示用户当前模式（如 L2 显示"已使用整条字幕"）

### 5.5 非标字幕格式检测与处理

**扫描时检测**（管理台扫描素材库阶段）：

| 字幕特征 | 检测方式 | 处理 |
|---|---|---|
| 标准 VTT | 首行 `WEBVTT` | 直接用 |
| 标准 SRT | 序号 + 时间戳格式 | 转 VTT 存储 |
| B站 CC JSON | 文件首字符 `{` 且含 `body` 字段 | Phase 0 拒绝，warning"请用 B 站字幕下载工具导出 srt/vtt" |
| ASS/SSA | 首行 `Script Info:` 或 `[Events]` | Phase 0 拒绝，warning"暂不支持 ASS/SSA，请转 srt" |
| 自动生成带样式标签的 WebVTT | 含 `<c>`、`<i>`、`<b>` 等 HTML 标签 | 入库时清洗掉样式标签 |

---

## 六、架构图

### 6.1 整体架构（ASCII）

```
┌──────────────────────────────────────────────────────────────────────┐
│  浏览器（用户本机 Windows）                                              │
│                                                                       │
│  ┌─────────────────────────────────┐    ┌────────────────────────┐    │
│  │  React 18 + Vite SPA (5173)    │    │  Ant Design 5           │    │
│  │                                 │    │                          │    │
│  │  ┌───────────────────────────┐  │    │  ┌──────────────────┐  │    │
│  │  │  ArtPlayer + 自定义字幕层  │  │    │  │  管理台 /admin    │  │    │
│  │  │  ┌─────────────────────┐  │  │    │  │  - 大模型配置    │  │    │
│  │  │  │  视频播放器           │  │  │    │  │  - 素材扫描      │  │    │
│  │  │  │  ┌───────────────┐   │  │  │    │  │  - 系统设置      │  │    │
│  │  │  │  │ 字幕 overlay   │   │  │  │    │  └──────────────────┘  │    │
│  │  │  │  │ (Selection API)│  │  │  │    │                          │    │
│  │  │  │  │ 右键 contextmenu│  │  │  │    │  ┌──────────────────┐  │    │
│  │  │  │  └───────────────┘   │  │  │    │  │  AI 对话侧边栏    │  │    │
│  │  │  └─────────────────────┘  │  │    │  │  (流式渲染 SSE)    │  │    │
│  │  └───────────────────────────┘  │    │  │  多轮历史(5轮)     │  │    │
│  │                                 │    │  └──────────────────┘  │    │
│  │  状态: Zustand (播放器+对话+素材)│    │                          │    │
│  └─────────────────────────────────┘    └────────────────────────┘    │
│                                  │                                     │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   │ /api/* /admin/* /materials/*
                                   │ (Vite proxy 同源)
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  后端 FastAPI (uvicorn 127.0.0.1:8000)                                │
│                                                                       │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────────┐     │
│  │  /admin         │  │  /api/materials │  │  /api/chat/stream   │     │
│  │  静态文件 serve │  │  扫描/列表/元数据│  │  SSE 反向代理        │     │
│  │  (管理台前端)   │  │  /video 流式    │  │  → context_builder  │     │
│  └────────────────┘  └────────────────┘  └──────────────────────┘     │
│                                                                       │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────────┐     │
│  │  /api/admin/*  │  │  /api/auth/login │  │  /api/config/*      │     │
│  │  素材管理       │  │  JWT 单密码登录  │  │  模型配置 CRUD      │     │
│  └────────────────┘  └────────────────┘  └──────────────────────┘     │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  context_builder.py  ← Phase 0→1 演进解耦点                  │   │
│  │  ├ 系统prompt（助学者模板）                                  │   │
│  │  ├ filter_courseware_by_time()  ← Phase 1 替换为向量检索      │   │
│  │  ├ extract_transcript_window() ← Phase 1 替换为向量检索      │   │
│  │  ├ 选中字幕片段 + 时间戳                                     │   │
│  │  └ 多轮历史（最近5轮，从 session 取）                         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────┐    │
│  │  SQLite       │  │  ./materials/    │  │  内存 cache (热更新) │    │
│  │  app.db       │  │  └ my-course/   │  │  model_configs_cache │    │
│  │  ├ configs    │  │    ├ video.mp4  │  │  写时失效            │    │
│  │  ├ materials  │  │    ├ subtitle.  │  │                      │    │
│  │  └ sessions   │  │    │  vtt        │  │                      │    │
│  │               │  │    └ courseware.│  │                      │    │
│  │  AES 加密 key │  │      md         │  │                      │    │
│  └──────────────┘  └──────────────────┘  └──────────────────────┘    │
│                                                                       │
│  安全: localhost 绑定 + JWT + AES 加密 key + 后端代理大模型请求         │
└──────────────────────────────────┬────────────────────────────────────┘
                                   │
                                   │ HTTPS POST
                                   │ {baseUrl}/v1/chat/completions
                                   │ stream=true
                                   │ Authorization: Bearer {API_KEY}
                                   │
                                   ▼
                ┌────────────────────────────────────┐
                │  大模型 API（OpenAI 兼容协议）       │
                │  DeepSeek / 通义 / 智谱 / Moonshot  │
                │  / 豆包 / OpenAI / 零一 / 百川...    │
                └────────────────────────────────────┘
```

### 6.2 核心数据流（一次 AI 提问完整链路）

```
[1] 用户播放视频
[2] 用户暂停（自动或手动）
[3] 用户鼠标选中屏幕字幕片段（Selection API）
[4] 用户右键 → "以此段字幕向 AI 提问"
       │
       ▼
[5] 前端 Zustand 触发 AI 提问
    payload: { course_id, selected_subtitle, start_time, end_time, user_question, session_id, model_config_id }
       │
       │ POST /api/chat/stream
       ▼
[6] 后端 /api/chat/stream
    ├ 6.1 从 SQLite 读 model_config（解密 API key）
    ├ 6.2 从 SQLite 读 material 元数据 + 课件文本缓存
    ├ 6.3 从 ./materials/{course_id}/subtitle.vtt 读逐字稿
    ├ 6.4 context_builder 构造 messages:
    │     - system: 助学者 prompt
    │     - system: 课件按章节粗筛
    │     - system: 逐字稿时间窗（±3分钟）
    │     - history: 最近5轮（从 chat_sessions）
    │     - user: 选中字幕 + 用户问题
    ├ 6.5 httpx.AsyncClient POST {baseUrl}/v1/chat/completions stream=true
    ├ 6.6 解析上游 SSE chunk → 重新打包成前端 SSE
    └ 6.7 累积完整响应写回 chat_sessions
       │
       │ StreamingResponse (SSE)
       ▼
[7] 前端 fetch + ReadableStream 逐字渲染到 AI 对话面板
[8] AI 输出完成，用户可继续追问（回到 [3] 或直接输入新问题）
```

### 6.3 Phase 0→1→2 演进架构

```
Phase 0（当前）          Phase 1（引入 RAG）         Phase 2+（引入 langchain）
─────────────           ─────────────               ─────────────
context_builder         context_builder             context_builder
├ 时间窗截取            ├ Chroma 向量检索           ├ langchain Retriever
├ 课件章节粗筛          ├ bge-m3 embedding          ├ Multi-query retriever
└ 自管对话历史          └ 自管对话历史              ├ Agent (工具调用)
                                                    └ Multi-step reasoning
                                                    + 引用标注 / 重排序
直调 OpenAI API         直调 OpenAI API              langchain LLM chain
httpx                   httpx                        langchain
```

---

## 七、Phase 0 Top 5 技术风险预案

### R-05 长课程 Token 超限导致核心链路断（高风险）

**风险描述**：1 小时以上课程逐字稿 + 课件 + 多轮历史累积超 32K 模型上下文上限，第一次提问就报错或答非所问，"可直接拿来用"承诺破产。

**发生概率**：高（真实课程 1 小时以上是常态）

**预案**：
1. **主方案**：时间窗截取（选中字幕时间戳 ±3 分钟逐字稿，详见 2.3.2）
2. **课件粗筛**：按选中字幕匹配最近章节，只传相关章节课件
3. **多轮历史限制**：最近 5 轮，超出提示用户清空会话
4. **管理台可选大上下文模型**：长课程场景下用户切到 128K 模型（DeepSeek、Claude 等）
5. **token 预检**：后端 `context_builder` 输出 messages 后估算 token 数，超阈值时管理台 warning 或自动截断

**降级**：
- 极端情况（课件无章节且 >2 万字）：截断到最近上下文 + 界面提示"课程过长，建议分段学习"
- 完全失败：返回友好错误，提示用户清空会话重试

---

### R-07 管理台安全风险（key 泄露 / 未授权访问）（高风险）

**风险描述**：大模型 API key 泄露导致额度被盗用；管理台裸奔被他人篡改配置。

**发生概率**：中（自用场景下低，但 Phase 0 demo 给 B 站评审时不可控）

**预案**：
1. **localhost 绑定**：FastAPI 启动绑定 127.0.0.1，禁止远程访问（第一道防线）
2. **单密码 + JWT**：管理台登录用单密码（bcrypt hash 存 SQLite），JWT 1 小时有效期
3. **AES-GCM 加密 API key**：密钥从环境变量 `APP_SECRET` 读，SQLite 里只存密文
4. **后端代理大模型请求**：前端永远不直接调大模型 API，前端代码搜索不到任何 key
5. **管理台 key mask 显示**：列表只显示 `sk-****1234`，编辑时不回显
6. **访问日志**：管理台所有写操作记日志（who/when/what），便于审计

**降级**：
- 完全关闭远程访问，仅本机使用
- key 怀疑泄露时管理台一键 rotate（生成新密钥，旧密钥失效）

---

### R-08 可换素材承载方式与浏览器沙箱冲突（高风险）

**风险描述**：H5 前端不能读取本地任意目录，用户换素材方式未定导致前后端无法开工。

**发生概率**：中（架构问题，方案 B 已定，主要风险是用户操作不熟悉）

**预案**：
1. **方案 B 主路径**：后端约定目录 `./materials/{course_id}/` + 管理台扫描按钮
2. **管理台扫描逻辑**：按扩展名识别三件套，srt 自动转 vtt，PDF 提取文本缓存
3. **格式约束清单**：vtt + srt 双支持，md + pdf 课件，mp4 + webm 视频（详见 4.3）
4. **扫描失败友好提示**：缺文件、格式不支持、扫描异常都有明确错误文案
5. **目录结构文档**：管理台首页展示示例目录结构 + 接入步骤

**降级**：
- 方案 C：前端 input file 运行时加载（无持久化，刷新丢失），适合 demo 快速试用
- 用户实在搞不定目录结构：提供一份"示例素材"一键加载（仓库自带 demo 课程素材）

---

### R-04 字幕选中交互兼容性差（中高风险）

**风险描述**：用户自备字幕格式多样（srt、ass、B 站 CC、自动生成带样式标签的 vtt），非标字幕无法选中，核心交互失效。

**发生概率**：高（用户拿到的字幕格式复杂）

**预案**：
1. **自定义渲染层**（详见第五章）：不用原生 `<track>`，自己渲染 cue div，用 Selection API 选中
2. **双格式支持**：vtt + srt 至少支持，srt 入库时自动转 vtt
3. **非标格式检测**：扫描时识别 ass/ssa/B 站 CC JSON，warning 提示转换工具
4. **样式标签清洗**：自动生成字幕常带 `<c>`、`<i>`、`<b>` 等 HTML 标签，入库时清洗

**降级（三级）**：
- L2：选中失败时取整条 cue 文本
- L3：无 cue 时取当前时间点 + 后端时间窗截取
- L4：纯手动输入 + "插入当前字幕"按钮

---

### R-09 多轮追问上下文管理导致 Token 累加超限（中风险）

**风险描述**：第 3-4 轮追问时历史累加超限，或丢历史导致答偏。

**发生概率**：高（多轮追问是核心交互，用户必然用到）

**预案**：
1. **历史轮数限制**：最近 5 轮（10 条 messages），超出自动丢弃最旧
2. **课程上下文每轮重新构造**：课件 + 逐字稿时间窗每轮重新计算，不累积
3. **token 预检**：构造 messages 后估算总 token，超阈值时截断历史（保留最近 3 轮）
4. **UI 提示**：对话面板显示"已保留最近 5 轮历史"，超过 5 轮时提示用户"建议清空会话重新开始"

**降级**：
- 严重超限时自动切换到单轮模式（不带历史），界面提示
- 用户可手动"清空会话"重新开始

---

## 八、开发执行建议

### 8.1 推荐开发顺序

| 顺序 | 模块 | 工作量 | 依赖 |
|---|---|---|---|
| 1 | 后端骨架（FastAPI + SQLite + 配置表） | 0.5 天 | 无 |
| 2 | 管理台鉴权 + 模型配置 CRUD | 1 天 | 1 |
| 3 | 素材扫描接口（srt→vtt + PDF 提取） | 1 天 | 1 |
| 4 | context_builder + SSE 大模型代理 | 1 天 | 2 |
| 5 | 前端骨架（Vite + React + Antd + Zustand） | 0.5 天 | 无 |
| 6 | ArtPlayer + 自定义字幕层 + 右键菜单 | 1.5 天 | 5 |
| 7 | AI 对话侧边栏（流式渲染） | 1 天 | 4、5 |
| 8 | 管理台前端（模型配置 + 素材扫描 UI） | 1 天 | 2、3、5 |
| 9 | 联调 + 错误处理 + 降级方案 | 1 天 | 全部 |
| **合计** | | **约 8.5 天** | |

### 8.2 关键里程碑

- **M1**：管理台能配置大模型 + 扫描素材（验证后端 + 数据库 + 鉴权）
- **M2**：前端能播放视频 + 选中字幕 + 右键（验证字幕选中核心风险 R-04）
- **M3**：完整链路跑通（视频→暂停→选中字幕→右键→AI 流式回答→多轮追问）

### 8.3 不在 Phase 0 做的事（明确边界）

- ❌ RAG（Phase 1）
- ❌ langchain（Phase 2+）
- ❌ 用户系统（Phase 1+）
- ❌ 文件上传（方案 A，Phase 1+）
- ❌ docx/pptx 课件（Phase 1+）
- ❌ Docker 部署（Phase 1+）
- ❌ 跨课程检索（Phase 1+）
- ❌ 讲师口吻 Few-shot 复刻（Phase 2+）
- ❌ 课程笔记生成 / 知识点总结（Phase 2+）
- ❌ Claude/Gemini 原生协议适配（Phase 1+）

---

## 九、附录：技术栈版本清单

| 组件 | 版本 | 备注 |
|---|---|---|
| Node.js | 20 LTS | Vite 5 要求 Node 18+ |
| Python | 3.13.12 | 用户本机已装 |
| React | 18.3 | 稳定版 |
| Vite | 5.x | 现代构建工具 |
| Ant Design | 5.x | 企业级 UI |
| Zustand | 4.x | 轻量状态管理 |
| ArtPlayer | 5.x | 开源播放器 |
| FastAPI | 0.110+ | Python web 框架 |
| uvicorn | 0.27+ | ASGI server |
| SQLAlchemy | 2.x | ORM |
| httpx | 0.27+ | 异步 HTTP 客户端（调大模型） |
| python-docx | 已装 | 读 docx |
| webvtt-py / pysrt | 最新 | 字幕解析转换 |
| pymupdf | 最新 | PDF 文本提取 |
| bcrypt | 最新 | 密码 hash |
| PyJWT | 最新 | JWT 令牌 |
| cryptography | 最新 | AES-GCM 加密 |

---

## 十、待用户确认事项

1. **前端框架**：本报告推荐 React 18 + Vite。若用户已是 Vue 熟手，可改 Vue 3 + Vite，方案不受影响。**请用户确认**。
2. **大模型默认配置**：Phase 0 默认用哪家大模型？建议 DeepSeek（成本低、32K/128K 可选、国内访问稳定），但需用户确认（取决于用户已有 API key）。
3. **管理台密码策略**：是否接受"环境变量配置 + 首次启动随机生成"方案？还是用户希望固定密码？
4. **素材目录路径**：`./materials/` 是否合适？是否需要支持自定义路径（管理台配置）？
5. **是否支持方案 C 降级**：前端 input file 运行时加载是否要做？还是 Phase 0 只做方案 B？

确认后即可进入开发。
