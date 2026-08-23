# AI 助学助手（Study Assistance Agent）

一个本地运行的 AI 助学产品：播放自备课程视频，选中字幕右键向 AI 提问，AI 结合课件与逐字稿时间窗答疑，支持多轮追问。管理员通过本地管理台配置大模型、上传素材、管理用户。

## 技术栈

- **前端**：React 18 + Vite + TypeScript + Ant Design 5 + Zustand + ArtPlayer
- **后端**：Python FastAPI + SQLAlchemy 2.x + SQLite
- **大模型**：OpenAI 兼容协议（默认阿里云百炼 / 通义千问），后端 SSE 代理
- **上下文策略**：字幕时间窗截取（±3 分钟）+ 课件章节粗筛（不上 RAG）
- **自动字幕**：Whisper（medium 模型，视频无字幕时自动生成）

## 目录结构

```
助学demo/
├── frontend/          # 前端（React + Vite）
│   └── src/
│       ├── api/       # axios 客户端（JWT 拦截器）
│       ├── store/     # Zustand（认证 / 主题）
│       ├── components/ # 字幕层 / AI 侧边栏 / 课程卡片 / 顶部导航
│       └── pages/     # 登录 / 首页 / 课程列表 / 播放页 / 管理台
├── backend/           # 后端（FastAPI）
│   ├── app/
│   │   ├── api/       # 路由（auth / materials / chat / admin）
│   │   ├── core/      # 配置 / 数据库 / 安全 / seed
│   │   ├── models/    # ORM 模型（5 张表）
│   │   └── services/  # 字幕 / 课件 / 存储 / Whisper / context_builder / llm
│   └── tests/         # pytest 单元测试
└── .env               # 环境变量（本地，不提交）
```

## 快速开始

### 1. 后端

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate   Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次启动自动建表并 seed 两个账号：`admin/123456`（管理员）、`user25/123456`（学习者）。

> 可选：安装 Whisper 自动字幕 `pip install openai-whisper`（需系统装有 ffmpeg）。

### 2. 前端

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173
```

前端 Vite 已配置 `/api` 代理到 `http://127.0.0.1:8000`。

### 3. 配置大模型

1. 用 `admin/123456` 登录 → 进入管理后台 → 模型配置
2. 新增配置（Base URL + API Key + 模型名），设为默认
3. 阿里云百炼默认：Base URL `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen-plus`

### 4. 上传素材

1. 管理后台 → 素材管理 → 上传文件
2. 上传视频（mp4/webm）+ 字幕（vtt/srt，无字幕自动 Whisper 生成）+ 课件（md/pdf/pptx）
3. 回到首页 → 点击课程 → 播放 → 选中字幕右键提问

## 运行测试

```bash
# 后端
cd backend && pytest tests/

# 前端
cd frontend && npm test
```

## 核心交互链路

播放视频 → 暂停 → 选中字幕 → 右键 → "以此段字幕向 AI 提问" → AI 结合课件+逐字稿时间窗流式答疑 → 多轮追问

字幕选中降级：L1 选中文本 / L2 整条字幕 / L3 时间戳 / L4 手动输入兜底。

## 环境变量

复制 `.env.example` 为 `.env`，填写大模型 API Key 等信息（`.env` 已被 gitignore 排除）。
