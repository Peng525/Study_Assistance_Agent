# Phase 0 架构评估报告 v2（盲点补充）

> 评估角色：前端 + 轻量后端架构师
> 评估对象：architecture.md (v1) + prd.md + 需求变更记录-001 + 项目技术准备方案 v4
> 文档定位：v1 的补充文档，不重复 v1 已确认的技术栈选型理由、RAG 决策、ArtPlayer 字幕渲染方案等
> 日期：2026-08-23
> 范围：补 4 个架构盲点 + 字幕=逐字稿架构影响修正

---

## 〇、v2 相对 v1 的变更概览

| 盲点 | v1 状态 | v2 补充 | 影响层级 |
|---|---|---|---|
| 1. 用户系统 | 单密码 + JWT | 双角色 JWT（admin/user）+ users 表 + seed + 重置密码 | 后端鉴权 + 前端路由守卫 |
| 2. PPT 课件 | 仅 md/pdf | 新增 .pptx（python-pptx 提取）| 素材扫描模块 |
| 3. 素材更新 | 方案 B 提及"重新扫描"未细化 | rescan 接口 + 缓存失效 + 进行中会话处理 | 素材管理模块 |
| 4. Whisper 字幕 | 用户自备字幕 | openai-whisper 自动生成 + 异步任务队列 | 新增整块后端能力 |
| 字幕=逐字稿 | v1 多处提"逐字稿"独立文件 | 统一为"字幕文件（即逐字稿）"，去 transcript_path | context_builder 数据源 |

---

## 一、盲点 1：用户系统架构

### 1.1 方案设计

v1 假设"管理台单密码鉴权"，PRD 升级为双角色 JWT + users 表。Phase 0 不做注册/忘记密码/邮件，预置两账号由后端首次启动 seed。

**鉴权链路**：

```
前端登录页 POST /api/auth/login {username, password}
  ↓
后端 verify_password(plaintext, user.password_hash) → bcrypt
  ↓ 通过
签发 JWT（payload: {user_id, username, role, exp}），有效期 1h
  ↓
前端存 localStorage['ai_edu_jwt']，后续请求 Authorization: Bearer <jwt>
  ↓
后端 get_current_user 依赖解析 JWT → 注入 user 对象
  ↓
require_admin 依赖进一步校验 role=='admin'，否则 403
```

**关键设计点**：

1. JWT 密钥从环境变量 `JWT_SECRET` 读，首次启动未配置则随机生成写入 `.env`
2. JWT payload 必含 `role` 字段，前端据此决定是否展示管理台入口
3. 密码用 bcrypt hash（cost factor=12），永不存明文
4. `/api/admin/*` 路由统一挂 `Depends(require_admin)` 依赖，单点校验，不在每个 handler 里重复写
5. 预置账号 seed 在首次启动时执行（检测 users 表为空则插入），后续重启不重复 seed

### 1.2 users 表 DDL

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,                          -- bcrypt hash
    role TEXT NOT NULL CHECK(role IN ('admin', 'user')),  -- 双角色约束
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_username ON users(username);
```

**字段说明**：
- `username` 唯一索引，登录时按 username 查
- `password_hash` bcrypt 格式 `$2b$12$...`，自带盐+cost factor，无需独立 salt 字段
- `role` 用 CHECK 约束保证只允许 `admin`/`user` 两值，避免脏数据
- 不存 email（Phase 0 不做忘记密码/邮件）
- 不存 last_login_at（Phase 0 不需要审计登录行为，Phase 1+ 再加）

### 1.3 SQLAlchemy 模型定义

```python
# backend/models/user.py
from sqlalchemy import Column, Integer, Text, Timestamp, func
from backend.db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text, nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False)  # 'admin' / 'user'，CHECK 约束由 DDL 保证
    created_at = Column(Timestamp, server_default=func.now())
    updated_at = Column(Timestamp, server_default=func.now(), onupdate=func.now())
```

### 1.4 JWT 签发与鉴权中间件（FastAPI Depends）

```python
# backend/auth/jwt.py
import os
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from backend.db import get_db
from backend.models.user import User

JWT_SECRET = os.getenv("JWT_SECRET")  # 首次启动若空，后端随机生成写入 .env
JWT_ALGO = "HS256"
JWT_TTL_SECONDS = 3600  # 1 小时

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def create_access_token(user: User) -> str:
    payload = {
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=JWT_TTL_SECONDS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    cred_err = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="JWT 无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        user_id = payload.get("user_id")
        if user_id is None:
            raise cred_err
    except jwt.ExpiredSignatureError:
        raise cred_err
    except jwt.InvalidTokenError:
        raise cred_err

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise cred_err
    return user

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user
```

**使用方式**：

```python
# 任意 user 可访问
@router.get("/api/materials")
def list_materials(current_user: User = Depends(get_current_user)):
    ...

# 仅 admin 可访问
@router.post("/api/admin/materials/scan")
def scan_materials(admin: User = Depends(require_admin)):
    ...
```

### 1.5 登录路由

```python
# backend/routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import bcrypt
from backend.db import get_db
from backend.models.user import User
from backend.auth.jwt import create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/login")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # form.username / form.password 由 FastAPI 从 form-data 解析
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not bcrypt.checkpw(
        form.password.encode("utf-8"), user.password_hash.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    token = create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
        },
    }

@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "role": current_user.role,
    }

@router.post("/logout")
def logout(current_user: User = Depends(get_current_user)):
    # Phase 0 无服务端会话，前端清 localStorage 即可；后端仅记录日志
    return {"msg": "已登出"}

@router.post("/change-password")
def change_password(
    payload: dict,  # {old_password, new_password}
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not bcrypt.checkpw(
        payload["old_password"].encode("utf-8"),
        current_user.password_hash.encode("utf-8"),
    ):
        raise HTTPException(status_code=400, detail="旧密码错误")
    if len(payload["new_password"]) < 6:
        raise HTTPException(status_code=400, detail="新密码不少于 6 位")
    current_user.password_hash = bcrypt.hashpw(
        payload["new_password"].encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("utf-8")
    db.commit()
    return {"msg": "密码已修改，请重新登录"}
```

### 1.6 admin 重置用户密码

```python
# backend/routers/admin_users.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import bcrypt
from backend.db import get_db
from backend.models.user import User
from backend.auth.jwt import require_admin

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

DEFAULT_PASSWORD = "123456"

@router.get("")
def list_users(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.id).all()
    return [
        {"id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at}
        for u in users
    ]

@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="不能重置自己的密码")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")

    target.password_hash = bcrypt.hashpw(
        DEFAULT_PASSWORD.encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("utf-8")
    db.commit()
    # Phase 0 简化：不写审计日志表，仅 stderr 打印
    import sys
    print(f"[admin] {admin.username} reset password of user {target.username}", file=sys.stderr)
    return {"msg": f"已重置为默认密码 {DEFAULT_PASSWORD}，请通知用户登录后修改"}
```

### 1.7 预置账号 seed 机制

```python
# backend/db/seed.py
import bcrypt
from sqlalchemy.orm import Session
from backend.models.user import User

PRESET_ACCOUNTS = [
    {"username": "admin", "password": "123456", "role": "admin"},
    {"username": "user25", "password": "123456", "role": "user"},
]

def seed_preset_accounts(db: Session) -> None:
    """首次启动时调用：users 表为空则插入预置账号"""
    if db.query(User).count() > 0:
        return
    for acc in PRESET_ACCOUNTS:
        hash_str = bcrypt.hashpw(
            acc["password"].encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")
        db.add(User(
            username=acc["username"],
            password_hash=hash_str,
            role=acc["role"],
        ))
    db.commit()
    import sys
    print("[seed] 预置账号已创建：admin/123456, user25/123456", file=sys.stderr)
```

**调用时机**：在 `main.py` 的 `lifespan` startup 钩子里调用一次。

```python
# backend/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from backend.db import engine, Base, SessionLocal
from backend.db.seed import seed_preset_accounts

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_preset_accounts(db)
    finally:
        db.close()
    yield

app = FastAPI(lifespan=lifespan)
```

### 1.8 前端路由守卫

```typescript
// src/router/guards.ts
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/auth'

export function RequireAuth({ children }: { children: JSX.Element }) {
  const token = useAuthStore((s) => s.token)
  const role = useAuthStore((s) => s.role)
  const location = useLocation()

  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  // admin 专属路由
  if (location.pathname.startsWith('/admin') && role !== 'admin') {
    return <Navigate to="/" replace />
  }
  return children
}

export function RedirectIfLoggedIn({ children }: { children: JSX.Element }) {
  const token = useAuthStore((s) => s.token)
  const role = useAuthStore((s) => s.role)
  if (token) {
    return <Navigate to={role === 'admin' ? '/admin' : '/'} replace />
  }
  return children
}
```

```typescript
// src/router/index.tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { RequireAuth, RedirectIfLoggedIn } from './guards'
import LoginPage from '@/pages/Login'
import CourseListPage from '@/pages/CourseList'
import CoursePlayerPage from '@/pages/CoursePlayer'
import AdminHomePage from '@/pages/admin/Home'
import AdminModelConfigsPage from '@/pages/admin/ModelConfigs'
import AdminMaterialsPage from '@/pages/admin/Materials'
import AdminUsersPage from '@/pages/admin/Users'

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<RedirectIfLoggedIn><LoginPage /></RedirectIfLoggedIn>} />
        <Route path="/" element={<RequireAuth><CourseListPage /></RequireAuth>} />
        <Route path="/course/:courseId" element={<RequireAuth><CoursePlayerPage /></RequireAuth>} />
        <Route path="/admin" element={<RequireAuth><AdminHomePage /></RequireAuth>} />
        <Route path="/admin/model-configs" element={<RequireAuth><AdminModelConfigsPage /></RequireAuth>} />
        <Route path="/admin/materials" element={<RequireAuth><AdminMaterialsPage /></RequireAuth>} />
        <Route path="/admin/users" element={<RequireAuth><AdminUsersPage /></RequireAuth>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
```

**JWT 解析与 store**：

```typescript
// src/stores/auth.ts
import { create } from 'zustand'
import { jwtDecode } from 'jwt-decode'

interface AuthState {
  token: string | null
  role: 'admin' | 'user' | null
  username: string | null
  setToken: (token: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('ai_edu_jwt'),
  role: null,
  username: null,
  setToken: (token: string) => {
    localStorage.setItem('ai_edu_jwt', token)
    const payload = jwtDecode<{ username: string; role: 'admin' | 'user' }>(token)
    set({ token, role: payload.role, username: payload.username })
  },
  logout: () => {
    localStorage.removeItem('ai_edu_jwt')
    set({ token: null, role: null, username: null })
  },
}))

// 启动时初始化 role/username（防刷新丢失）
const initialToken = localStorage.getItem('ai_edu_jwt')
if (initialToken) {
  try {
    const payload = jwtDecode<{ username: string; role: 'admin' | 'user' }>(initialToken)
    useAuthStore.setState({ role: payload.role, username: payload.username })
  } catch {
    localStorage.removeItem('ai_edu_jwt')
  }
}
```

**axios 拦截器（自动带 JWT + 401 跳登录）**：

```typescript
// src/api/client.ts
import axios from 'axios'
import { useAuthStore } from '@/stores/auth'

const client = axios.create({ baseURL: '/' })

client.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      useAuthStore.getState().logout()
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default client
```

### 1.9 JWT 存储位置与有效期

| 项 | 决策 | 理由 |
|---|---|---|
| 存储 | `localStorage['ai_edu_jwt']` | 跨标签页共享、刷新保留；不用 cookie 避免 CSRF 复杂度 |
| 有效期 | 1 小时 | 短期令牌，过期重新登录；不做 refresh token（Phase 0 简化）|
| 过期处理 | 后端 401 → 前端拦截器清 JWT + 跳 `/login` | 单点处理，避免每个请求重复判断 |
| XSS 风险 | 接受 | Phase 0 自用 + 本机绑定 127.0.0.1，XSS 攻击面极小；Phase 1+ 可迁 httpOnly cookie |

### 1.10 风险预案

| 风险 | 触发场景 | 预案 |
|---|---|---|
| JWT 密钥泄露 | `.env` 被误提交 | `.gitignore` 强制忽略 `.env`；密钥泄露后换 `JWT_SECRET` 重启，所有 token 立即失效 |
| 预置账号未 seed | users 表已有数据但不是预置账号 | seed 函数检测 `count()==0` 才插入，避免覆盖；若用户手动删过表，提供 `python -m backend.db.reseed` 命令强制重置 |
| admin 锁死自己密码 | admin 改密后忘记 | 已禁止 admin 重置自己；admin 改自己密码走 `/api/auth/change-password`，需旧密码验证；极端情况手动改 SQLite `password_hash` 字段为 bcrypt('123456') |
| JWT 过期频繁打断学习 | 1h 过期用户正看到一半 | 前端拦截器在过期前 5 分钟静默提示"令牌即将过期，请保存会话"；过期后跳登录页保留当前 course_id/session_id，重新登录后回到原页 |
| 越权访问 `/api/admin/*` | user 拿到 admin 接口 URL 直接调 | `require_admin` 依赖统一拦截，返回 403；不在 handler 内重复判断 |
| bcrypt cost factor 过高导致登录慢 | rounds=12 单次验证 ~300ms | Phase 0 可接受；若部署到弱机器降到 rounds=10 |

---

## 二、盲点 2：PPT 课件支持架构

### 2.1 方案设计

v1 仅支持 md/pdf 课件，PRD 新增 .pptx。PPT 文本提取用 `python-pptx`，按页分割，页标题作为章节分隔点。

**集成流程**：

```
materials 扫描
  ↓ 识别 courseware 文件扩展名
  ├─ .md  → 直接入库 courseware_text_cached（原文）
  ├─ .pdf → pymupdf 提取文本 → 入库
  └─ .pptx → python-pptx 遍历 slides → 提取每页标题+正文 → 拼接带章节标记的文本 → 入库
  ↓
courseware_has_chapters 判定：
  ├─ md：含 `^#{1,6} ` 标题行 → true
  ├─ pdf：pymupdf 拿到 TOC 书签 → true
  └─ pptx：slides 数量 > 1 且至少 2 页有非空标题 → true
```

### 2.2 python-pptx 集成

```bash
pip install python-pptx
```

**依赖加入 `requirements.txt`**：

```
python-pptx>=0.6.23
```

### 2.3 PPT 文本提取规则

**提取策略**：
1. 遍历 `presentation.slides`，每页一个章节
2. 每页优先取 `slide.shapes.title.text` 作为章节标题（若占位符存在且非空）
3. 遍历 `slide.shapes`，取所有 text frame 的纯文本（排除标题占位符避免重复）
4. 拼接格式：每页输出 `## 第 N 页：{标题}\n\n{正文}\n\n`，与 Markdown 章节格式统一
5. 图片型 PPT（提取出空文本）→ `courseware_text_cached=''` + `courseware_has_chapters=false` + 管理台 warning

```python
# backend/services/courseware_pptx.py
from pptx import Presentation
from typing import Tuple

def extract_pptx_text(pptx_path: str) -> Tuple[str, bool]:
    """
    提取 PPT 文本，返回 (文本, 是否有章节结构)
    文本格式：每页一个 Markdown 章节，便于后续 chapter 粗筛复用 md 逻辑
    """
    prs = Presentation(pptx_path)
    pages = []
    pages_with_title = 0

    for idx, slide in enumerate(prs.slides, start=1):
        # 1. 提取标题
        title_text = ""
        if slide.shapes.title and slide.shapes.title.has_text_frame:
            title_text = slide.shapes.title.text_frame.text.strip()

        if title_text:
            pages_with_title += 1

        # 2. 提取正文（所有非标题占位符的 text frame）
        body_parts = []
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            # 跳过标题占位符
            if shape == slide.shapes.title:
                continue
            txt = shape.text_frame.text.strip()
            if txt:
                body_parts.append(txt)

        body_text = "\n".join(body_parts)

        # 3. 拼装成 Markdown 章节格式
        if title_text:
            pages.append(f"## 第 {idx} 页：{title_text}\n\n{body_text}")
        else:
            pages.append(f"## 第 {idx} 页\n\n{body_text}")

    full_text = "\n\n".join(pages)
    has_chapters = len(prs.slides) > 1 and pages_with_title >= 2
    return full_text, has_chapters
```

### 2.4 章节识别策略（与 md/pdf 统一）

context_builder 的 `filter_courseware_by_time` 需要章节切分。Phase 0 统一用"Markdown 风格章节标记"，PPT 提取时已转成 `## 第 N 页：标题`，可与 md 共用同一套切分逻辑。

```python
# backend/services/courseware_chapter.py
import re

def split_courseware_into_chapters(courseware_text: str) -> list[dict]:
    """
    把课件文本切成章节列表，每章含 title 和 content
    适用于 md / pdf（提取后带 # 标题）/ pptx（已转 ## 第 N 页：标题）
    返回: [{title: str, content: str, chapter_idx: int}]
    """
    chapters = []
    # 匹配 Markdown 标题行（## 或 # 开头）
    pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    matches = list(pattern.finditer(courseware_text))

    if not matches:
        # 无章节结构，返回单章
        return [{"title": "全文", "content": courseware_text, "chapter_idx": 0}]

    for i, m in enumerate(matches):
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(courseware_text)
        content = courseware_text[start:end].strip()
        chapters.append({
            "title": title,
            "content": content,
            "chapter_idx": i,
        })
    return chapters

def filter_courseware_by_time(
    courseware_text: str,
    selected_time: float,
    chapter_timestamps: list[dict] | None,  # PPT/md 无时间戳，传 None
) -> str:
    """
    按选中字幕时间戳粗筛课件章节
    PPT 和 md 没有时间戳，无法精确匹配章节 → 走 fallback 策略：
    1. 有 chapter_timestamps（从字幕章节标题提取） → 取最近章节 + 前后各 1 章
    2. 无 chapter_timestamps → 取章节总数的前 30%（默认策略）或全文
    Phase 0 简化：无时间戳时返回全文（已在 courseware_has_chapters=false 路径处理）
    """
    chapters = split_courseware_into_chapters(courseware_text)
    if len(chapters) <= 1:
        return courseware_text

    # Phase 0 简化策略：课件有章节但无时间戳映射，返回全文（已 token 预检兜底）
    # Phase 1+ RAG 上线后此处替换为向量检索
    return courseware_text
```

### 2.5 materials 扫描流程更新（增加 .pptx 分支）

```python
# backend/services/material_scanner.py（增量补充 pptx 分支）
from pathlib import Path
from backend.services.courseware_pdf import extract_pdf_text  # v1 已有
from backend.services.courseware_pptx import extract_pptx_text  # 新增
from backend.services.courseware_md import extract_md_text     # md 直读

COURSEWARE_EXTS = {'.md', '.pdf', '.pptx'}  # v1 是 {.md, .pdf}，v2 加 .pptx

def extract_courseware(courseware_path: Path) -> tuple[str, str, bool]:
    """
    返回 (format, text, has_chapters)
    format: 'md' / 'pdf' / 'ppt'
    """
    ext = courseware_path.suffix.lower()
    if ext == '.md':
        text = extract_md_text(courseware_path)
        has_chapters = bool(re.search(r'^#{1,6}\s+', text, re.MULTILINE))
        return 'md', text, has_chapters
    elif ext == '.pdf':
        text, has_chapters = extract_pdf_text(courseware_path)  # pymupdf + TOC 检测
        return 'pdf', text, has_chapters
    elif ext == '.pptx':
        text, has_chapters = extract_pptx_text(courseware_path)
        return 'ppt', text, has_chapters
    else:
        raise ValueError(f"不支持的课件格式: {ext}")
```

**courseware_format 字段取值更新**：v1 是 `'md'/'pdf'`，v2 加 `'ppt'`（与 PRD 9.3 节对齐）。

### 2.6 图片型 PPT 处理

图片型 PPT（整页是图片，无文本占位符）python-pptx 提取出空字符串。处理策略：

```python
def extract_courseware_with_warning(courseware_path: Path):
    fmt, text, has_chapters = extract_courseware(courseware_path)
    warning = None
    if fmt == 'ppt' and not text.strip():
        warning = "PPT 未提取到任何文本，可能是图片型 PPT。建议用 OCR 处理或改用 PDF 课件"
    elif fmt == 'pdf' and not text.strip():
        warning = "PDF 未提取到文本，可能是扫描版 PDF。建议提供可复制文本的 PDF"
    return fmt, text, has_chapters, warning
```

管理台扫描结果列表的 `error_message` 字段记录 warning（非致命，status 仍为 ready）。

### 2.7 courseware_text_cached 缓存策略

| 字段 | 写入时机 | 失效时机 |
|---|---|---|
| `courseware_text_cached` | 扫描/重新扫描时一次性写入 | rescan 时覆盖写 |
| `courseware_has_chapters` | 扫描时根据格式判定 | rescan 时重新判定 |
| `courseware_format` | 扫描时按扩展名填 | rescan 时若换格式（如 md→pptx）则更新 |

**缓存读取**：`/api/chat/stream` 调用 context_builder 时直接从 SQLite 读 `courseware_text_cached`，不重新提取（避免每次提问都跑 python-pptx）。

### 2.8 courseware_has_chapters 判定逻辑

| 格式 | 判定条件 | has_chapters=true 示例 |
|---|---|---|
| md | 文本中含至少 1 个 `^#{1,6}\s+` 标题行 | `# 第一章 入门\n...\n# 第二章 进阶` |
| pdf | pymupdf 拿到非空 TOC 书签 | PDF 大纲有章节 |
| pptx | slides 数量 > 1 且至少 2 页有非空标题占位符 | 10 页 PPT，8 页有标题 |

`has_chapters=false` 时 context_builder 走"全文传入"分支 + 管理台 warning 提示"课件无章节结构，长课程可能超 token 限制"。

### 2.9 风险预案

| 风险 | 触发场景 | 预案 |
|---|---|---|
| python-pptx 提取文本丢失排版 | 表格、SmartArt、图表 | 接受降级，PPT 本质是页面集合，文本提取足够支撑 AI 答疑；管理台 warning"排版信息已丢失，仅保留文本" |
| 图片型 PPT 提取空文本 | 全图片 PPT | warning 提示 + courseware_text_cached='' + has_chapters=false；AI 仍可基于字幕时间窗答疑（不传课件） |
| PPT 文件损坏 | 上传一半的 pptx | python-pptx 抛 `PackageNotFoundError`，扫描时 catch → status=error + error_message |
| 大 PPT 提取慢 | 100+ 页 PPT | python-pptx 单页提取 <10ms，100 页 <1s，可接受；超 200 页 warning |
| PPT 中文乱码 | 罕见，python-pptx 默认 utf-8 | 测试中文 PPT 验证；若有乱码用 `python-pptx` 的 `encoding` 参数兜底 |
| 占位符类型多样 | title 占位符可能为空但布局有标题文本 | 已处理：title_text 为空时章节标题用 `第 N 页`，正文仍提取所有 text frame |

---

## 三、盲点 3：素材更新流程架构

### 3.1 方案设计

v1 方案 B 提及"重新扫描"但未细化接口、缓存失效、进行中会话处理。v2 补齐。

**rescan 与 scan 的关系**：
- `scan`（全量）：扫描 `./materials/*/`，发现新目录则插入 materials 表；已存在目录按 rescan 逻辑刷新
- `rescan`（单课程）：仅扫描指定 `course_id` 目录，覆盖刷新该行所有字段
- 两者共用 `scan_course_dir(course_id)` 内核函数，差异仅在外层循环和入库策略

### 3.2 rescan 接口设计

```python
# backend/routers/admin_materials.py
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from backend.db import get_db
from backend.models.user import User
from backend.models.material import Material
from backend.auth.jwt import require_admin
from backend.services.material_scanner import scan_course_dir, trigger_whisper_if_needed

router = APIRouter(prefix="/api/admin/materials", tags=["admin-materials"])

@router.post("/scan")
def scan_all(
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """全量扫描 ./materials/*/，新目录插入，已存在目录走 rescan 逻辑"""
    import os
    materials_dir = os.getenv("MATERIALS_DIR", "./materials")
    results = []
    if not os.path.isdir(materials_dir):
        return {"msg": "materials 目录不存在", "results": []}

    for course_id in os.listdir(materials_dir):
        course_path = os.path.join(materials_dir, course_id)
        if not os.path.isdir(course_path):
            continue
        result = scan_course_dir(db, course_id)
        results.append(result)
        # 无字幕的视频入队 Whisper（详见盲点 4）
        if result.get("needs_whisper"):
            background_tasks.add_task(trigger_whisper_if_needed, course_id)

    return {"msg": f"扫描完成，共 {len(results)} 个课程", "results": results}

@router.post("/{course_id}/rescan")
def rescan_one(
    course_id: str,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """重新扫描单课程：覆盖刷新所有字段，缓存失效重建"""
    existing = db.query(Material).filter(Material.course_id == course_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail=f"课程 {course_id} 不存在，请用 scan 接口")

    result = scan_course_dir(db, course_id, force_refresh=True)
    if result.get("needs_whisper"):
        background_tasks.add_task(trigger_whisper_if_needed, course_id)

    return {
        "msg": f"课程 {course_id} 已重新扫描",
        "result": result,
        "scanned_at": result.get("scanned_at"),
    }
```

### 3.3 scan_course_dir 内核函数（共用）

```python
# backend/services/material_scanner.py
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.orm import Session
from backend.models.material import Material
from backend.services.subtitle_converter import convert_srt_to_vtt
from backend.services.courseware_pptx import extract_pptx_text  # 盲点 2
from backend.services.courseware_pdf import extract_pdf_text
from backend.services.courseware_md import extract_md_text
import os

VIDEO_EXTS = {'.mp4', '.webm'}
SUBTITLE_EXTS = {'.vtt', '.srt'}
COURSEWARE_EXTS = {'.md', '.pdf', '.pptx'}

def scan_course_dir(db: Session, course_id: str, force_refresh: bool = False) -> dict:
    """
    扫描单个课程目录，刷新 materials 表该行
    force_refresh=True 时强制覆盖（rescan 场景）
    返回扫描结果 dict，含 needs_whisper 标志
    """
    materials_root = os.getenv("MATERIALS_DIR", "./materials")
    course_dir = Path(materials_root) / course_id

    if not course_dir.is_dir():
        return {"course_id": course_id, "status": "error", "error_message": "目录不存在"}

    # 1. 识别文件
    video_path = _find_first(course_dir, VIDEO_EXTS)
    subtitle_path = _find_first(course_dir, SUBTITLE_EXTS)
    courseware_path = _find_first(course_dir, COURSEWARE_EXTS)

    error_msg = None
    warning = None

    if not video_path:
        return {"course_id": course_id, "status": "error", "error_message": "未找到视频文件"}

    # 2. 字幕处理：srt → vtt 转换
    subtitle_source_format = None
    needs_whisper = False
    if subtitle_path:
        if subtitle_path.suffix.lower() == '.srt':
            vtt_path = subtitle_path.with_suffix('.vtt')
            convert_srt_to_vtt(subtitle_path, vtt_path)
            subtitle_path = vtt_path
            subtitle_source_format = 'srt'
        else:
            subtitle_source_format = 'vtt'
    else:
        # 无字幕文件，标记需要 Whisper 生成
        needs_whisper = True
        subtitle_path = None
        subtitle_source_format = None

    # 3. 课件处理
    courseware_format = None
    courseware_text = ""
    has_chapters = False
    if courseware_path:
        ext = courseware_path.suffix.lower()
        if ext == '.md':
            courseware_format = 'md'
            courseware_text = extract_md_text(courseware_path)
            has_chapters = bool(__import__('re').search(r'^#{1,6}\s+', courseware_text, __import__('re').MULTILINE))
        elif ext == '.pdf':
            courseware_format = 'pdf'
            courseware_text, has_chapters = extract_pdf_text(courseware_path)
            if not courseware_text.strip():
                warning = "PDF 未提取到文本，可能是扫描版 PDF"
        elif ext == '.pptx':
            courseware_format = 'ppt'
            courseware_text, has_chapters = extract_pptx_text(courseware_path)
            if not courseware_text.strip():
                warning = "PPT 未提取到文本，可能是图片型 PPT"

    # 4. 写入/更新 materials 表
    material = db.query(Material).filter(Material.course_id == course_id).first()
    scanned_at = datetime.now(timezone.utc)

    if material:
        # rescan：覆盖刷新所有字段（保留 id）
        material.dir_path = str(course_dir)
        material.video_path = str(video_path) if video_path else None
        material.subtitle_path = str(subtitle_path) if subtitle_path else None
        material.subtitle_source_format = subtitle_source_format
        material.courseware_path = str(courseware_path) if courseware_path else None
        material.courseware_format = courseware_format
        material.courseware_text_cached = courseware_text
        material.courseware_has_chapters = has_chapters
        material.status = 'ready' if not error_msg else 'error'
        material.error_message = error_msg or warning
        material.scanned_at = scanned_at
        # 字幕状态字段（盲点 4）
        if needs_whisper and material.subtitle_status != 'ready':
            material.subtitle_status = 'pending'
            material.subtitle_source = None
        elif subtitle_path:
            material.subtitle_status = 'ready'
            material.subtitle_source = 'manual'
    else:
        material = Material(
            course_id=course_id,
            dir_path=str(course_dir),
            video_path=str(video_path) if video_path else None,
            subtitle_path=str(subtitle_path) if subtitle_path else None,
            subtitle_source_format=subtitle_source_format,
            courseware_path=str(courseware_path) if courseware_path else None,
            courseware_format=courseware_format,
            courseware_text_cached=courseware_text,
            courseware_has_chapters=has_chapters,
            status='ready' if not error_msg else 'error',
            error_message=error_msg or warning,
            scanned_at=scanned_at,
            subtitle_status='pending' if needs_whisper else 'ready',
            subtitle_source=None if needs_whisper else 'manual',
        )
        db.add(material)

    db.commit()
    return {
        "course_id": course_id,
        "status": material.status,
        "error_message": material.error_message,
        "scanned_at": scanned_at.isoformat(),
        "needs_whisper": needs_whisper,
    }

def _find_first(directory: Path, extensions: set) -> Path | None:
    """在目录中找第一个扩展名匹配的文件"""
    for f in directory.iterdir():
        if f.is_file() and f.suffix.lower() in extensions:
            return f
    return None
```

### 3.4 缓存失效策略

rescan 时所有缓存字段一次性覆盖写，无脏读：

| 字段 | 失效方式 |
|---|---|
| `courseware_text_cached` | 覆盖写新提取的全文 |
| `subtitle_path` | srt→vtt 重新转换并覆盖 |
| `video_path` | 重新识别（用户可能换了视频文件名）|
| `courseware_has_chapters` | 重新判定 |
| `scanned_at` | 更新为当前时间 |
| `subtitle_status` | 若新扫描有字幕文件 → `ready`；无字幕 → `pending`（等 Whisper）|
| `subtitle_source` | 有字幕文件 → `manual`；Whisper 生成 → `whisper` |

### 3.5 进行中 AI 会话的处理

**核心策略**：rescan 不主动中断进行中的 AI 会话，但下次提问用新缓存。前端检测 `scanned_at` 变化时提示用户。

**后端实现**：
- `/api/chat/stream` 每次请求都从 SQLite 实时读 `materials` 行（不内存 cache 课程上下文），自然用最新缓存
- `chat_sessions` 表增加 `last_scanned_at` 字段，记录会话创建时的素材扫描时间；提问时若 `materials.scanned_at > chat_sessions.last_scanned_at` → 返回 warning 字段提示前端

```python
# backend/routers/chat.py 增量补充
@router.post("/stream")
async def chat_stream(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    course = db.query(Material).filter(Material.course_id == payload["course_id"]).first()
    if not course:
        raise HTTPException(404, "课程不存在")

    session = db.query(ChatSession).filter(
        ChatSession.session_id == payload["session_id"]
    ).first()

    context_warning = None
    if session and course.scanned_at > session.last_scanned_at:
        context_warning = "素材已在会话期间更新，建议清空会话重试以保证上下文一致性"

    # 正常构造 messages（用最新 course.courseware_text_cached）
    messages = build_messages(course, payload, session)
    # ... 流式返回，首个 chunk 带 context_warning
```

**前端实现**：

```typescript
// src/stores/chat.ts
import { client } from '@/api/client'

interface ChatState {
  sessionId: string | null
  lastScannedAt: string | null
  contextWarning: string | null
  // ...
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessionId: null,
  lastScannedAt: null,
  contextWarning: null,

  checkMaterialUpdate: async (courseId: string) => {
    // 进入课程页时拉取最新 scanned_at，与本地存的对比
    const res = await client.get(`/api/materials/${courseId}`)
    const stored = get().lastScannedAt
    if (stored && res.data.scanned_at > stored) {
      set({
        contextWarning: '素材已更新，建议清空会话重试以保证上下文一致性',
      })
    }
    set({ lastScannedAt: res.data.scanned_at })
  },

  clearSession: async () => {
    // 用户点"清空会话"按钮
    await client.post(`/api/chat/sessions/${get().sessionId}/clear`)
    set({ contextWarning: null })
  },
}))
```

### 3.6 更新失败回滚策略

**决策**：保留旧缓存 + 标记 warning，不清空。

**理由**：
- 清空缓存会让该课程完全不可用（AI 答疑无课件上下文）
- 保留旧缓存至少能维持"降级可用"，用户可继续学习旧内容
- warning 提示让 admin 知道该课程需要重新处理

```python
def scan_course_dir_safe(db: Session, course_id: str, force_refresh: bool = False) -> dict:
    """带 try-catch 的扫描，失败时保留旧缓存"""
    existing = db.query(Material).filter(Material.course_id == course_id).first()
    try:
        return scan_course_dir(db, course_id, force_refresh)
    except Exception as e:
        # 失败：保留旧缓存，标记 warning
        if existing:
            existing.error_message = f"重新扫描失败: {str(e)}，已保留旧缓存"
            existing.status = 'ready'  # 仍是 ready，可继续用旧缓存
            db.commit()
        return {
            "course_id": course_id,
            "status": "warning",
            "error_message": f"重新扫描失败: {str(e)}，已保留旧缓存",
        }
```

### 3.7 并发控制

**决策**：rescan 时**不阻塞**该课程的其他操作，但加文件锁防止并发 rescan 同一课程。

**理由**：
- rescan 耗时 <1s（PDF/PPT 文本提取 + srt→vtt 转换），阻塞影响小
- AI 会话是流式 SSE，长连接，若阻塞会导致连接超时
- 但并发 rescan 同一课程会产生写冲突（同时覆盖 courseware_text_cached）

**实现**：用进程内 dict 锁（Phase 0 单进程，不需要分布式锁）：

```python
# backend/services/scan_lock.py
import threading
from collections import defaultdict

_scan_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_scan_locks_guard = threading.Lock()

def get_scan_lock(course_id: str) -> threading.Lock:
    with _scan_locks_guard:
        return _scan_locks[course_id]

# 在 rescan_one 中使用
@router.post("/{course_id}/rescan")
def rescan_one(course_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    lock = get_scan_lock(course_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, f"课程 {course_id} 正在扫描中，请稍后再试")
    try:
        result = scan_course_dir_safe(db, course_id, force_refresh=True)
        return {"msg": "重新扫描完成", "result": result}
    finally:
        lock.release()
```

### 3.8 风险预案

| 风险 | 触发场景 | 预案 |
|---|---|---|
| 旧缓存与新数据不一致 | rescan 中途失败 | 3.6 保留旧缓存 + warning；下次 rescan 成功后覆盖 |
| 用户在 rescan 期间提问 | AI 会话读到半新半旧数据 | SQLite 单写多读，事务隔离；rescan 在一个事务内 commit，提问读到的要么是旧完整数据要么是新完整数据，无半新半旧 |
| 同时 rescan 同一课程 | admin 双击按钮 | 进程内锁返回 409，前端按钮 loading 状态防双击 |
| 进行中会话上下文突变 | 用户正在 AI 对话，admin 改了课件 | 3.5 后端检测 scanned_at 变化返回 warning，前端提示清空会话 |
| rescan 时视频文件被占用 | 视频正在播放，Windows 文件锁 | 视频流式读取用 `FileResponse` 不锁文件；rescan 只读元数据不修改视频文件 |
| 字幕文件被替换但旧 srt→vtt 转换缓存未清 | 用户换了新 srt 但 vtt 是旧的 | rescan 时强制重新转换 srt→vtt，覆盖旧 vtt 文件 |

---

## 四、盲点 4：Whisper 自动字幕生成架构

### 4.1 方案设计（最复杂的补充）

**核心挑战**：Whisper 字幕生成耗时长（1h 视频 CPU 约 30-60min），不能阻塞 HTTP 请求，需要异步任务 + 进度反馈 + 资源控制。

**整体架构**：

```
[1] 素材扫描发现无字幕视频
    ↓
[2] materials.subtitle_status = 'pending'，入队 Whisper 任务
    ↓
[3] 后台 worker 检测队列，串行处理（同时只跑 1 个 Whisper 任务）
    ↓
[4] whisper.load_model('medium') → transcribe(video_path) → write_srt/vtt
    ↓
[5] 生成完成：subtitle_status='ready'，subtitle_source='whisper'，subtitle_path 指向生成的 vtt
    ↓
[6] 前端轮询 GET /api/materials/{course_id}/subtitle-status 显示进度
```

### 4.2 openai-whisper 集成

```bash
pip install openai-whisper
# 依赖 ffmpeg（whisper 用 ffmpeg 解码音频），用户需本机装 ffmpeg 并加 PATH
```

**`requirements.txt` 新增**：

```
openai-whisper>=20231117
```

**ffmpeg 依赖说明**：whisper 内部用 `subprocess.run(['ffmpeg', ...])` 抽取音频，用户需自行安装：
- Windows：`winget install ffmpeg` 或下载 https://www.gyan.dev/ffmpeg/builds/ 解压加 PATH
- 启动时后端检测 `ffmpeg` 是否在 PATH，缺失则 stderr warning

```python
# backend/services/whisper_check.py
import shutil
import sys

def check_ffmpeg() -> bool:
    if not shutil.which('ffmpeg'):
        print("[warn] ffmpeg 未在 PATH 中，Whisper 字幕生成将无法运行", file=sys.stderr)
        print("[warn] Windows 安装：winget install ffmpeg 或 https://www.gyan.dev/ffmpeg/builds/", file=sys.stderr)
        return False
    return True
```

### 4.3 模型选型

| 模型 | 大小 | 中文效果 | CPU 1h 视频耗时 | GPU 1h 视频耗时 | 推荐场景 |
|---|---|---|---|---|---|
| tiny | 39MB | 差 | ~3min | ~30s | 极速验证 |
| base | 74MB | 一般 | ~6min | ~1min | 短视频 demo |
| small | 244MB | 中等 | ~15min | ~2min | 英文为主 |
| **medium** | **769MB** | **好** | **~30-60min** | **~5min** | **中文主推（Phase 0 默认）** |
| large | 1.5GB | 极好 | ~60-120min | ~10min | 极致质量，Phase 0 不推荐（CPU 太慢）|

**Phase 0 决策**：默认 `medium` 模型，管理台配置项可切换（admin 在 system_settings 里改 `whisper_model` 值）。

**理由**：
- 中文识别准确率 medium 已足够好（>90%），large 提升有限但 CPU 耗时翻倍
- 769MB 模型文件可接受（首次下载一次，后续复用）
- CPU 30-60min 可接受（Phase 0 自用场景，用户放视频后会离开做别的事）

### 4.4 异步任务设计（核心决策）

**方案对比**：

| 方案 | 优点 | 缺点 | Phase 0 适配度 |
|---|---|---|---|
| FastAPI BackgroundTasks | 内置无依赖，代码极简 | 重启丢失任务；无任务状态查询；无并发控制；无重试 | ❌ 不推荐（重启丢任务是硬伤） |
| Celery + Redis | 生产级，任务持久化、重试、并发控制 | 引入 Redis 服务依赖；Phase 0 单机过度设计；运维成本高 | ❌ 不推荐 |
| **线程池 + 状态轮询** | 无外部依赖；状态可查（写 SQLite）；并发可控（队列长度=1）；重启后 pending 任务可恢复 | 需自己实现队列和状态机；进程重启时 generating 状态需手动恢复 | ✅ **推荐** |
| asyncio + aiojobs | 异步原生 | Whisper 是 CPU 密集型同步任务，asyncio 无收益；且会阻塞事件循环 | ❌ 不推荐 |

**决策：线程池 + 状态轮询**

**理由**：
1. Phase 0 单机部署，无 Redis 依赖负担
2. Whisper 是 CPU 密集型同步任务，线程池跑后台 worker 不阻塞 FastAPI 主事件循环
3. 队列长度=1（同时只跑 1 个 Whisper 任务）避免内存爆炸（medium 模型加载占 ~2GB 内存）
4. 任务状态写 SQLite `materials.subtitle_status` 字段，重启后可恢复
5. 进程重启时 `generating` 状态的任务自动回滚为 `pending`，下次启动重新入队

### 4.5 字幕生成流程详解

```
[1] scan 时识别无字幕视频
    ├─ materials.subtitle_status = 'pending'
    └─ whisper_queue.put(course_id)  # 入队

[2] whisper_worker 后台线程（启动时 daemon=True 启动）
    ├─ while True: course_id = whisper_queue.get()  # 阻塞等任务
    ├─ 更新 subtitle_status = 'generating'
    ├─ 加载模型（首次加载 ~10s，后续复用内存缓存）
    ├─ whisper.transcribe(video_path, language='zh', task='transcribe')
    ├─ 写 srt + vtt 文件到 ./materials/{course_id}/whisper-subtitle.{srt,vtt}
    ├─ 更新 materials: subtitle_path = .../whisper-subtitle.vtt
    │                  subtitle_status = 'ready'
    │                  subtitle_source = 'whisper'
    │                  subtitle_source_format = 'vtt'
    └─ 任务完成，处理下一个

[3] 失败处理
    ├─ try/except 捕获异常
    ├─ subtitle_status = 'error'
    ├─ error_message = str(e)
    └─ 不自动重试（避免循环失败），admin 可手动触发 POST /api/admin/materials/{course_id}/generate-subtitle

[4] 前端轮询
    ├─ GET /api/materials/{course_id}/subtitle-status
    └─ 返回 {status: 'pending'/'generating'/'ready'/'error', progress?: int, error?: str}
```

### 4.6 后端实现：Whisper 任务管理器

```python
# backend/services/whisper_worker.py
import os
import queue
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.db import SessionLocal
from backend.models.material import Material

# 全局任务队列（进程内单例）
_whisper_queue: queue.Queue = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()
_model_cache = {}  # {model_name: whisper_model}，进程级缓存避免重复加载

# 进度上报（按视频时长百分比估算）
_progress_map: dict[str, int] = {}  # {course_id: 0-100}

def enqueue_whisper_task(course_id: str) -> None:
    """入队一个 Whisper 任务"""
    _whisper_queue.put(course_id)
    print(f"[whisper] 入队任务: {course_id}", flush=True)

def start_whisper_worker() -> None:
    """启动后台 worker 线程（在 FastAPI lifespan 里调用一次）"""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        t = threading.Thread(target=_whisper_worker_loop, daemon=True, name="whisper-worker")
        t.start()
        print("[whisper] 后台 worker 已启动", flush=True)

def _whisper_worker_loop():
    """worker 主循环：从队列取任务，串行执行"""
    while True:
        course_id = _whisper_queue.get()  # 阻塞等待
        try:
            _process_one_course(course_id)
        except Exception as e:
            print(f"[whisper] 任务异常 course_id={course_id}: {e}", flush=True)
            _mark_error(course_id, str(e))
        finally:
            _whisper_queue.task_done()

def _process_one_course(course_id: str):
    """处理单个课程的字幕生成"""
    db = SessionLocal()
    try:
        material = db.query(Material).filter(Material.course_id == course_id).first()
        if not material:
            print(f"[whisper] 课程不存在: {course_id}", flush=True)
            return
        if material.subtitle_status == 'ready':
            print(f"[whisper] 已有字幕，跳过: {course_id}", flush=True)
            return

        # 1. 标记 generating
        material.subtitle_status = 'generating'
        material.error_message = None
        db.commit()
        _progress_map[course_id] = 0

        # 2. 加载模型（首次加载慢，后续从内存缓存取）
        model_name = os.getenv("WHISPER_MODEL", "medium")
        model = _get_or_load_model(model_name)

        # 3. 调用 transcribe（CPU 密集，会阻塞当前线程）
        video_path = material.video_path
        if not video_path or not os.path.isfile(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        print(f"[whisper] 开始转写 {course_id} (model={model_name})", flush=True)
        # 启动一个进度上报线程（Whisper 本身不暴露进度，用时长估算）
        progress_thread = threading.Thread(
            target=_estimate_progress, args=(course_id, video_path), daemon=True
        )
        progress_thread.start()

        result = model.transcribe(video_path, language='zh', task='transcribe', verbose=False)

        # 4. 写 srt + vtt 文件
        course_dir = Path(material.dir_path)
        srt_path = course_dir / 'whisper-subtitle.srt'
        vtt_path = course_dir / 'whisper-subtitle.vtt'

        _write_srt(result['segments'], srt_path)
        _srt_to_vtt_file(srt_path, vtt_path)

        # 5. 更新 materials 表
        material.subtitle_path = str(vtt_path)
        material.subtitle_source_format = 'vtt'
        material.subtitle_status = 'ready'
        material.subtitle_source = 'whisper'
        material.error_message = None
        db.commit()

        _progress_map[course_id] = 100
        print(f"[whisper] 完成 {course_id}，字幕写入 {vtt_path}", flush=True)
    finally:
        db.close()
        _progress_map.pop(course_id, None)

def _get_or_load_model(model_name: str):
    """模型缓存，避免每次任务重新加载"""
    if model_name not in _model_cache:
        import whisper
        print(f"[whisper] 加载模型 {model_name}（首次加载约 10s）", flush=True)
        _model_cache[model_name] = whisper.load_model(model_name)
    return _model_cache[model_name]

def _estimate_progress(course_id: str, video_path: str):
    """估算进度：用 ffprobe 拿视频时长，按 30min/1h 估算进度"""
    try:
        import subprocess, json
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', video_path],
            capture_output=True, text=True, timeout=10
        )
        duration = float(json.loads(result.stdout)['format']['duration'])
        # medium 模型 CPU 约 0.5x 实时（1h 视频约 30-60min）
        estimated_total = duration * 0.7  # 保守估算 0.7x 实时
        start_time = time.time()
        while course_id in _progress_map and _progress_map[course_id] < 100:
            elapsed = time.time() - start_time
            progress = min(95, int(elapsed / estimated_total * 100))
            _progress_map[course_id] = progress
            time.sleep(5)  # 5 秒更新一次
    except Exception as e:
        print(f"[whisper] 进度估算失败 {course_id}: {e}", flush=True)

def _write_srt(segments, srt_path):
    """把 whisper segments 写成 srt 文件"""
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(segments, start=1):
            start = _format_srt_time(seg['start'])
            end = _format_srt_time(seg['end'])
            f.write(f"{i}\n{start} --> {end}\n{seg['text'].strip()}\n\n")

def _format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _srt_to_vtt_file(srt_path, vtt_path):
    """srt 转 vtt（复用 v1 的转换逻辑）"""
    with open(srt_path, 'r', encoding='utf-8') as f:
        srt_content = f.read()
    vtt_content = srt_to_vtt(srt_content)  # 复用 v1 的 srt_to_vtt 函数
    with open(vtt_path, 'w', encoding='utf-8') as f:
        f.write(vtt_content)

def _mark_error(course_id: str, error_msg: str):
    db = SessionLocal()
    try:
        material = db.query(Material).filter(Material.course_id == course_id).first()
        if material:
            material.subtitle_status = 'error'
            material.error_message = f"Whisper 生成失败: {error_msg}"
            db.commit()
    finally:
        db.close()

def get_subtitle_progress(course_id: str) -> int:
    return _progress_map.get(course_id, 0)

def recover_pending_tasks_on_startup():
    """启动时恢复：把 generating 状态的任务回滚为 pending 并重新入队"""
    db = SessionLocal()
    try:
        stuck = db.query(Material).filter(Material.subtitle_status == 'generating').all()
        for m in stuck:
            m.subtitle_status = 'pending'
            enqueue_whisper_task(m.course_id)
            print(f"[whisper] 恢复中断任务: {m.course_id}", flush=True)
        if stuck:
            db.commit()
        # 同时把 pending 但未入队的也入队
        pending = db.query(Material).filter(Material.subtitle_status == 'pending').all()
        for m in pending:
            enqueue_whisper_task(m.course_id)
    finally:
        db.close()

# 复用 v1 的 srt_to_vtt 函数（从 backend/services/subtitle_converter.py 导入）
from backend.services.subtitle_converter import srt_to_vtt
```

### 4.7 materials 表字段更新

```sql
-- v1 字段基础上新增 3 个字段
ALTER TABLE materials ADD COLUMN subtitle_status TEXT DEFAULT 'ready'
  CHECK(subtitle_status IN ('pending', 'generating', 'ready', 'error'));
ALTER TABLE materials ADD COLUMN subtitle_source TEXT
  CHECK(subtitle_source IN ('manual', 'whisper') OR subtitle_source IS NULL);
ALTER TABLE materials ADD COLUMN subtitle_error TEXT;
```

**完整 materials 表 DDL（v2 最终版）**：

```sql
CREATE TABLE materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL UNIQUE,
    dir_path TEXT NOT NULL,
    video_path TEXT,
    subtitle_path TEXT,                           -- 字幕文件路径（=逐字稿路径，变更6）
    subtitle_source_format TEXT,                 -- 'vtt' 或 'srt'（原始格式）
    subtitle_status TEXT NOT NULL DEFAULT 'ready'
        CHECK(subtitle_status IN ('pending', 'generating', 'ready', 'error')),
    subtitle_source TEXT
        CHECK(subtitle_source IN ('manual', 'whisper') OR subtitle_source IS NULL),
    subtitle_error TEXT,                          -- Whisper 生成失败原因
    courseware_path TEXT,
    courseware_format TEXT,                       -- 'md'/'pdf'/'ppt'
    courseware_text_cached TEXT,
    courseware_has_chapters BOOL,
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK(status IN ('ready', 'error')),
    error_message TEXT,
    scanned_at TIMESTAMP,
    -- 不再有 transcript_path（变更6：字幕=逐字稿）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_materials_course_id ON materials(course_id);
CREATE INDEX idx_materials_subtitle_status ON materials(subtitle_status);
```

### 4.8 模型文件管理

**模型存储位置**：`~/.cache/whisper/`（whisper 默认路径，跨用户复用）

| 模型 | 文件名 | 大小 |
|---|---|---|
| tiny | `~/.cache/whisper/tiny.pt` | 39MB |
| base | `~/.cache/whisper/base.pt` | 74MB |
| small | `~/.cache/whisper/small.pt` | 244MB |
| **medium** | `~/.cache/whisper/medium.pt` | **769MB** |
| large | `~/.cache/whisper/large-v3.pt` | 1.5GB |

**首次下载行为**：
- whisper.load_model('medium') 首次调用时会下载到 `~/.cache/whisper/medium.pt`
- 下载无进度条（whisper 用 urllib 内部下载），用户会感觉"卡住"
- **Phase 0 优化**：管理台首页显示"Whisper 模型状态"卡片，检测 `~/.cache/whisper/medium.pt` 是否存在 + 文件大小

```python
# backend/routers/admin_whisper.py
import os
from pathlib import Path
from fastapi import APIRouter, Depends
from backend.models.user import User
from backend.auth.jwt import require_admin

router = APIRouter(prefix="/api/admin/whisper", tags=["admin-whisper"])

@router.get("/model-status")
def model_status(admin: User = Depends(require_admin)):
    """检查 Whisper 模型文件状态"""
    model_name = os.getenv("WHISPER_MODEL", "medium")
    cache_dir = Path.home() / '.cache' / 'whisper'
    model_file = cache_dir / f"{model_name}.pt"

    if model_file.exists():
        size_mb = model_file.stat().st_size / 1024 / 1024
        return {
            "model_name": model_name,
            "downloaded": True,
            "size_mb": round(size_mb, 1),
            "path": str(model_file),
        }
    return {
        "model_name": model_name,
        "downloaded": False,
        "hint": f"首次生成字幕时会自动下载 {model_name} 模型（约 {_expected_size_mb(model_name)}MB）",
    }

def _expected_size_mb(model_name: str) -> int:
    return {'tiny': 39, 'base': 74, 'small': 244, 'medium': 769, 'large': 1500}.get(model_name, 769)
```

### 4.9 性能预估表

| 视频时长 | 模型 | CPU（i5-12 代）| GPU（RTX 3060）| 备注 |
|---|---|---|---|---|
| 10min | tiny | ~30s | ~5s | 极速验证用 |
| 10min | medium | ~5-10min | ~30s | Phase 0 默认 |
| 30min | medium | ~15-30min | ~2min | 中等课程 |
| **1h** | **medium** | **~30-60min** | **~5min** | **Phase 0 主战场** |
| 1.5h | medium | ~45-90min | ~8min | 长课程 |
| 1h | large | ~60-120min | ~10min | 不推荐 CPU |
| 3h | medium | ~90-180min | ~15min | 极长课程，建议拆分 |

**结论**：
- Phase 0 默认 medium + CPU，1h 视频约 30-60min，可接受（用户放视频后会离开）
- 若用户有 NVIDIA GPU，自动检测 CUDA 并启用 GPU 推理（whisper 自动用 torch.cuda）
- 超过 1.5h 的视频建议用户拆分成多 P

### 4.10 降级方案

**Whisper 生成失败时**：

1. `subtitle_status='error'` + `subtitle_error` 字段记录原因
2. 管理台素材列表显示"字幕生成失败"红色标记 + 错误原因
3. 提供"重新生成"按钮（POST /api/admin/materials/{course_id}/generate-subtitle）
4. 提供"手动上传字幕"入口：admin 把 srt/vtt 文件放到 `./materials/{course_id}/` 目录后重新 rescan
5. 学习端对该课程显示"字幕生成中/失败，暂不可用"，禁止进入播放页（PRD U2 AC3：状态非 ready 不展示）

**典型失败场景与提示**：

| 错误 | 提示文案 | 处理建议 |
|---|---|---|
| ffmpeg 未安装 | "ffmpeg 未在 PATH，Whisper 无法运行" | 管理台首页 warning + 安装指引链接 |
| 模型下载失败 | "Whisper 模型下载失败，请检查网络" | 提供手动下载链接 + 放到 `~/.cache/whisper/` |
| 显存/内存不足 | "内存不足，请关闭其他程序后重试" | 建议切 tiny/small 模型 |
| 视频文件损坏 | "视频文件无法解码" | 检查视频文件完整性 |
| 超时（>2h 未完成） | "生成超时，请用短视频测试" | 拆分视频或换小模型 |

### 4.11 API 设计

#### 4.11.1 素材扫描时自动触发

```python
# 已在 3.3 scan_course_dir 中实现：
# - 无字幕视频 → subtitle_status='pending' + 入队 Whisper
# - scan 接口返回 needs_whisper 标志，前端据此提示用户
```

#### 4.11.2 字幕生成状态查询

```python
# backend/routers/materials.py
from fastapi import APIRouter, Depends, HTTPException
from backend.models.user import User
from backend.auth.jwt import get_current_user
from backend.models.material import Material
from backend.services.whisper_worker import get_subtitle_progress
from sqlalchemy.orm import Session
from backend.db import get_db

router = APIRouter(prefix="/api/materials", tags=["materials"])

@router.get("/{course_id}/subtitle-status")
def subtitle_status(
    course_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查询字幕生成状态（前端轮询用）"""
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if not material:
        raise HTTPException(404, "课程不存在")

    return {
        "course_id": course_id,
        "status": material.subtitle_status,  # pending/generating/ready/error
        "source": material.subtitle_source,  # manual/whisper/null
        "progress": get_subtitle_progress(course_id) if material.subtitle_status == 'generating' else None,
        "error": material.subtitle_error if material.subtitle_status == 'error' else None,
    }
```

#### 4.11.3 手动触发生成

```python
# backend/routers/admin_whisper.py（追加）
from backend.services.whisper_worker import enqueue_whisper_task
from backend.models.material import Material
from sqlalchemy.orm import Session
from backend.db import get_db

@router.post("/materials/{course_id}/generate-subtitle")
def generate_subtitle(
    course_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """手动触发 Whisper 字幕生成（admin 在管理台点"重新生成"按钮）"""
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if not material:
        raise HTTPException(404, "课程不存在")
    if material.subtitle_status == 'generating':
        raise HTTPException(409, "该课程正在生成字幕，请勿重复触发")

    # 重置状态并入队
    material.subtitle_status = 'pending'
    material.subtitle_error = None
    db.commit()
    enqueue_whisper_task(course_id)
    return {"msg": f"已入队字幕生成任务: {course_id}"}
```

#### 4.11.4 前端轮询实现

```typescript
// src/hooks/useSubtitleStatus.ts
import { useEffect, useState, useRef } from 'react'
import { client } from '@/api/client'

interface SubtitleStatus {
  status: 'pending' | 'generating' | 'ready' | 'error'
  source: 'manual' | 'whisper' | null
  progress: number | null
  error: string | null
}

export function useSubtitleStatus(courseId: string | undefined) {
  const [status, setStatus] = useState<SubtitleStatus | null>(null)
  const timerRef = useRef<number>()

  useEffect(() => {
    if (!courseId) return

    const poll = async () => {
      try {
        const res = await client.get(`/api/materials/${courseId}/subtitle-status`)
        setStatus(res.data)
        // ready/error 时停止轮询
        if (res.data.status === 'ready' || res.data.status === 'error') {
          if (timerRef.current) clearInterval(timerRef.current)
          return
        }
      } catch (e) {
        console.error('字幕状态查询失败', e)
      }
    }

    poll()  // 首次立即查
    timerRef.current = window.setInterval(poll, 3000)  // 3 秒一次

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [courseId])

  return status
}
```

### 4.12 资源控制（同时只允许 1 个 Whisper 任务）

**实现**：用 `queue.Queue` 串行处理（worker 单线程取任务），天然保证同时只跑 1 个。

```python
# 队列长度无限制（任务堆积可接受，串行处理）
# 但内存控制：模型加载一次后常驻内存，单任务内存峰值 ~2-3GB（medium 模型 + 视频解码）
# 多任务不会并发加载模型 → 不会内存爆炸

# 进度查询接口可扩展返回队列位置
@router.get("/{course_id}/subtitle-status")
def subtitle_status(...):
    # ...
    queue_position = _whisper_queue.qsize() if material.subtitle_status == 'pending' else 0
    return {
        # ...
        "queue_position": queue_position,  # 前面还有几个任务
    }
```

**极端情况**：
- 用户一次扫描 10 个无字幕视频 → 10 个任务入队 → worker 串行处理 → 第 10 个可能等 5-10h
- 前端显示队列位置，用户知道预期等待时间
- admin 可在管理台"取消排队中的任务"（POST /api/admin/whisper/cancel/{course_id}，仅限 pending 状态）

### 4.13 启动时恢复逻辑

```python
# backend/main.py 的 lifespan 启动钩子
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    # 1. seed 预置账号
    db = SessionLocal()
    try:
        seed_preset_accounts(db)
    finally:
        db.close()

    # 2. 检查 ffmpeg
    from backend.services.whisper_check import check_ffmpeg
    check_ffmpeg()

    # 3. 启动 Whisper worker
    from backend.services.whisper_worker import start_whisper_worker, recover_pending_tasks_on_startup
    start_whisper_worker()
    recover_pending_tasks_on_startup()  # 恢复中断的任务

    yield

app = FastAPI(lifespan=lifespan)
```

### 4.14 风险预案

| 风险 | 触发场景 | 预案 |
|---|---|---|
| Whisper 安装失败 | Windows 编译 torch 出错 | 提供预编译 wheel 安装指引：`pip install torch --index-url https://download.pytorch.org/whl/cpu` |
| 模型下载卡住 | 国内访问 HuggingFace 慢 | 提供手动下载镜像链接（hf-mirror.com）+ 放到 `~/.cache/whisper/` |
| CPU 推理过慢 | 1h 视频 >2h | 管理台可切 tiny/small 模型；或建议用户拆分视频 |
| 内存爆炸 | 多个 Whisper 并发 | 已用单 worker 队列串行处理，杜绝并发 |
| 进程重启丢任务 | 用户 Ctrl+C 关后端 | 启动时 recover_pending_tasks_on_startup 把 generating 回滚为 pending 重新入队 |
| 队列堆积过多 | 一次扫描 10+ 无字幕视频 | 前端显示队列位置；admin 可取消 pending 任务 |
| 字幕质量差 | medium 模型对专业术语识别错 | 接受降级，用户可手动改 vtt 文件后 rescan；Phase 1+ 加 fine-tune |
| 视频时长超 3h | 超长视频生成时间不可接受 | 管理台 warning"视频过长，建议拆分" + 拒绝入队（Phase 0 软限制）|
| ffmpeg 版本不兼容 | 旧版 ffmpeg 编码问题 | 要求 ffmpeg 4.0+，启动时检测版本 |
| GPU 显存不足 | large 模型 + 长视频 | 自动降级到 CPU；或建议切 medium 模型 |

---

## 五、字幕=逐字稿的架构影响（变更6）

### 5.1 概念统一

**v1 问题**：v1 多处提到"逐字稿"作为独立文件，与"字幕"分开。导致数据模型可能需要 `transcript_path` 字段，素材接入要求用户准备 4 个文件（视频+字幕+逐字稿+课件），工程量与用户体验双输。

**v2 确认**：字幕文件（VTT/SRT）就是逐字稿。一份文件，两个用途：
1. 播放器加载显示为字幕（cue 渲染）
2. context_builder 解析为逐字稿（时间窗截取文本）

### 5.2 v1 文档表述修正

| v1 表述 | v2 修正 |
|---|---|
| "逐字稿"独立文件 | "字幕文件（即逐字稿）" |
| `materials.transcript_path` 字段（隐含） | 删除，统一用 `subtitle_path` |
| 素材三件套：视频+字幕+课件 | 仍三件套，字幕兼逐字稿 |
| context_builder 从 transcript 文件取文本 | context_builder 从 subtitle_path 对应的 VTT 文件取文本 |

### 5.3 context_builder 实现修正

```python
# backend/services/context_builder.py（v2 修正版）
from pathlib import Path
import webvtt  # webvtt-py

def extract_transcript_window(
    subtitle_path: str,          # 直接用 subtitle_path，不再有 transcript_path
    center_time: float,
    window_minutes: int = 3,
) -> str:
    """
    从字幕文件（即逐字稿）提取时间窗内的文本
    center_time: 选中字幕的起始时间（秒）
    window_minutes: 前后各 N 分钟
    """
    if not subtitle_path:
        return ""

    start = max(0, center_time - window_minutes * 60)
    end = center_time + window_minutes * 60

    cues = []
    try:
        for cue in webvtt.read(subtitle_path):
            cue_start = _parse_time(cue.start)
            cue_end = _parse_time(cue.end)
            # cue 与时间窗有交集则纳入
            if cue_end >= start and cue_start <= end:
                cues.append(cue.text.strip())
    except Exception as e:
        print(f"[context_builder] 字幕解析失败: {e}", flush=True)
        return ""

    return "\n".join(cues)

def _parse_time(time_str: str) -> float:
    """把 '00:01:23.456' 解析成秒"""
    h, m, s = time_str.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)

def build_messages(course, selected_subtitle, user_question, history):
    """
    v2 修正：transcript 来源直接用 course.subtitle_path
    """
    # 1. 课件粗筛
    courseware_text = filter_courseware_by_time(
        course.courseware_text_cached,
        selected_subtitle.start_time,
        None,
    )

    # 2. 字幕时间窗（变更6：直接读 subtitle_path）
    transcript_window = extract_transcript_window(
        subtitle_path=course.subtitle_path,  # 直接用字幕文件
        center_time=selected_subtitle.start_time,
        window_minutes=3,
    )

    # 3. 多轮历史
    recent_history = history[-5:] if len(history) > 5 else history

    # 4. 拼 messages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"课件内容：\n{courseware_text}"},
        {"role": "system", "content": f"字幕（即逐字稿，当前知识点前后片段）：\n{transcript_window}"},
        *recent_history,
        {"role": "user", "content": f"选中字幕：{selected_subtitle.text}\n\n我的问题：{user_question}"},
    ]
    return messages
```

### 5.4 数据模型简化

**materials 表删除字段**（v1 隐含但 v2 明确不要）：
- ~~`transcript_path`~~ → 统一用 `subtitle_path`

**影响**：
- PRD 9.3 节 materials 表 DDL 不含 transcript_path ✅ 已正确
- 素材接入文档只需用户准备 3 个文件：视频 + 字幕 + 课件
- Whisper 生成的字幕文件直接作为逐字稿使用，无需额外处理

---

## 六、更新后的开发顺序与工期估算

### 6.1 开发顺序（v2 最终版）

| 顺序 | 模块 | 工作量 | 依赖 | 变更说明 |
|---|---|---|---|---|
| 1 | 后端骨架（FastAPI + SQLite + SQLAlchemy + users 表 + seed）| 0.5 天 | 无 | v1 基础上加 users 表与 seed |
| 2 | 用户系统（JWT 签发/鉴权中间件 + 登录/登出/me/改密 + admin 重置密码）| 1 天 | 1 | **盲点 1 新增** |
| 3 | 前端骨架 + 路由守卫 + 登录页 + axios 拦截器 | 0.5 天 | 无 | v1 基础上加路由守卫 |
| 4 | 管理台模型配置 CRUD + 鉴权 | 0.5 天 | 2 | v1 已有，加 require_admin |
| 5 | 素材扫描接口（srt→vtt + md/pdf/pptx 提取 + rescan）| 1.5 天 | 1 | **盲点 2、3 补充 pptx + rescan** |
| 6 | Whisper 集成（worker + 队列 + 模型管理 + 状态查询 API）| 2 天 | 5 | **盲点 4 新增** |
| 7 | context_builder + SSE 大模型代理（字幕=逐字稿）| 1 天 | 4、5 | v1 基础上修正数据源 |
| 8 | ArtPlayer + 自定义字幕层 + 右键菜单 | 1.5 天 | 3 | v1 不变 |
| 9 | AI 对话侧边栏（流式渲染 + 字幕状态轮询）| 1 天 | 7、8 | v1 基础上加字幕状态轮询 |
| 10 | 管理台前端（模型配置 + 素材扫描 + 用户管理 + Whisper 状态）| 1.5 天 | 4、5、6 | **盲点 1、4 新增页面** |
| 11 | 联调 + 错误处理 + 降级方案 + Whisper 端到端测试 | 1.5 天 | 全部 | v1 基础上加 Whisper 测试 |
| **合计** | | **约 13-14 天** | | |

### 6.2 工期对比

| 版本 | 工期 | 增量来源 |
|---|---|---|
| v1 原估 | 8.5 天 | 核心链路 + 管理台 + 素材 |
| v4 变更后 | 9.5-10.5 天 | +用户系统简化版 +PPT +素材更新 +字幕交互模板 |
| **v2 含 Whisper** | **13-14 天** | **+Whisper 集成 2 天 + 状态轮询/降级 0.5 天 + 联调测试增量 1 天** |

### 6.3 关键里程碑（v2）

- **M1**：用户系统能登录 + admin 能配模型 + 扫描素材（验证盲点 1、2、3 后端）
- **M2**：前端能播放视频 + 选中字幕 + 右键（验证字幕选中核心风险 R-04，与 v1 一致）
- **M3**：完整 AI 链路跑通（视频→暂停→选中字幕→右键→AI 流式回答→多轮追问，与 v1 一致）
- **M4**（v2 新增）：无字幕视频放入 → 扫描 → Whisper 自动生成字幕 → 字幕就绪 → 学习端可用

### 6.4 不在 Phase 0 做的事（v2 边界更新）

v1 第 8.3 节"不做清单"基础上：
- ~~❌ 用户系统（Phase 1+）~~ → **v2 已纳入 Phase 0**（简化版）
- ~~❌ docx/pptx 课件（Phase 1+）~~ → **v2 已纳入 pptx**（docx 仍不做）
- ❌ Whisper 模型 fine-tune（Phase 2+）
- ❌ Whisper GPU 自动检测切换（Phase 0 用环境变量手动配置）
- ❌ Whisper 任务持久化到独立任务表（Phase 0 用 materials.subtitle_status 字段足够）
- ❌ 多 Whisper 任务并发（Phase 0 串行，Phase 1+ 视资源情况放开）

---

## 七、架构 v2 自检清单

### 7.1 4 个盲点覆盖度自检

- [x] **盲点 1 用户系统**：
  - [x] users 表 DDL（1.2 节）
  - [x] JWT 双角色签发与鉴权中间件（1.4 节，FastAPI Depends 实现）
  - [x] 登录路由 POST /api/auth/login（1.5 节）
  - [x] admin 重置 user 密码接口（1.6 节）
  - [x] 前端路由守卫（admin 进 /admin，user 进 /，未登录进 /login）（1.8 节）
  - [x] 预置账号 seed 机制（1.7 节，首次启动创建 admin/123456 + user25/123456）
  - [x] 密码 bcrypt hash 方案（1.4 节，cost factor=12）
  - [x] JWT 存储位置（localStorage）和有效期（1h）（1.9 节）
  - [x] 风险预案（1.10 节）

- [x] **盲点 2 PPT 课件支持**：
  - [x] python-pptx 集成方案（2.2 节）
  - [x] PPT 文本提取规则（按页分割，识别标题占位符）（2.3 节）
  - [x] 章节识别策略（页标题作为章节分隔点）（2.4 节）
  - [x] 扫描流程更新（.pptx 识别和文本提取）（2.5 节）
  - [x] 图片型 PPT 处理（warning 提示）（2.6 节）
  - [x] courseware_text_cached 缓存策略（2.7 节）
  - [x] courseware_has_chapters 判定逻辑（PPT 有多页标题则 true）（2.8 节）
  - [x] 风险预案（2.9 节）

- [x] **盲点 3 素材更新流程**：
  - [x] rescan 接口设计（POST /api/admin/materials/{course_id}/rescan）（3.2 节）
  - [x] 缓存失效策略（3.4 节）
  - [x] 进行中 AI 会话处理（前端检测 scanned_at 变化 → 提示用户清空会话）（3.5 节）
  - [x] 更新失败回滚策略（保留旧缓存 + warning）（3.6 节）
  - [x] 并发控制（rescan 时不阻塞其他操作，进程内锁防并发 rescan 同一课程）（3.7 节）
  - [x] 风险预案（3.8 节）

- [x] **盲点 4 Whisper 自动字幕生成**：
  - [x] openai-whisper 集成方案（4.2 节，含 ffmpeg 依赖说明）
  - [x] 异步任务设计（线程池+状态轮询，含 BackgroundTasks/Celery/线程池 三方案对比与推荐）（4.4 节）
  - [x] 字幕生成流程（5 步详解）（4.5 节）
  - [x] materials 表字段更新（subtitle_status/subtitle_source/subtitle_error）（4.7 节）
  - [x] 模型文件管理（~/.cache/whisper/，首次下载 769MB，管理台显示状态）（4.8 节）
  - [x] 性能预估表（不同视频长度 × 不同模型 × CPU/GPU）（4.9 节）
  - [x] 降级方案（Whisper 失败时提示手动上传字幕）（4.10 节）
  - [x] API 设计（自动触发 + 状态查询 + 手动触发，共 3 个接口）（4.11 节）
  - [x] 资源控制（同时只允许 1 个 Whisper 任务，串行队列）（4.12 节）
  - [x] 启动时恢复逻辑（4.13 节）
  - [x] 风险预案（10 类风险）（4.14 节）

### 7.2 字幕=逐字稿（变更6）落地自检

- [x] materials 表无 transcript_path 字段（4.7 节 DDL 确认）
- [x] context_builder 直接从 subtitle_path 读 VTT 取文本（5.3 节）
- [x] v1 中"逐字稿"表述统一为"字幕文件（即逐字稿）"（5.2 节对照表）
- [x] 素材接入仍三件套（视频+字幕+课件），字幕兼逐字稿（5.1 节）

### 7.3 与 v1 兼容性自检

- [x] 不推翻 v1 已确认技术栈（React 18 + Vite + Antd 5 + Zustand + ArtPlayer / FastAPI + SQLite + SQLAlchemy / 阿里通义 / 时间窗截取 / 本地部署 / 方案B）
- [x] 不推翻 v1 的 RAG 决策（Phase 0 不上 RAG）
- [x] 不推翻 v1 的字幕选中方案（ArtPlayer 自定义渲染层）
- [x] 不推翻 v1 的 SSE 流式代理方案
- [x] v1 第 3.3 节"单密码 + JWT"鉴权升级为"双角色 JWT + users 表"，原 JWT 机制保留
- [x] v1 第 4.3 节格式约束清单扩展（pptx 加入，docx 仍不做）
- [x] v1 第 8.1 节开发顺序扩展（增加用户系统/Whisper 等模块）

### 7.4 与 PRD 对齐自检

- [x] PRD 第 5.6 节用户系统模块 → v2 第一章覆盖
- [x] PRD 第 9.1 节 users 表 DDL → v2 第 1.2 节对齐（字段一致）
- [x] PRD 第 10.1 节用户接口 → v2 第 1.5、1.6 节实现全部 5 个接口
- [x] PRD 第 5.5 节 PPT 课件支持 → v2 第二章覆盖
- [x] PRD 第 4.4 节素材更新流程 → v2 第三章覆盖
- [x] PRD 第 10.4 节 rescan 接口 → v2 第 3.2 节实现
- [x] PRD 第 2 节用户角色（admin/user25）→ v2 第 1.7 节 seed 实现
- [x] 需求变更记录-001 变更 5 Whisper → v2 第四章覆盖
- [x] 需求变更记录-001 变更 6 字幕=逐字稿 → v2 第五章覆盖

### 7.5 工程量自检

- [x] v1 原 8.5 天 + 用户系统 1 天 + PPT 0.5 天 + 素材更新 0.5 天 + 字幕交互模板（已在 v1）= 9.5-10.5 天（与 v4 一致）
- [x] + Whisper 集成 2 天 + 状态轮询/降级 0.5 天 + 联调测试增量 1 天 = **13-14 天**（与变更记录汇总一致）

### 7.6 风险预案完整性自检

- [x] 每个盲点都含独立风险预案表（1.10 / 2.9 / 3.8 / 4.14）
- [x] Whisper 风险预案覆盖 10 类场景（安装/下载/性能/内存/重启/队列/质量/超时/ffmpeg/显存）
- [x] 与 v1 第 7 章 Top 5 风险预案无冲突（R-04/R-05/R-07/R-08/R-09 仍由 v1 方案覆盖）

---

## 八、待用户确认事项（v2 新增）

1. **Whisper 模型默认选型**：v2 默认 `medium`（769MB，CPU 1h 视频 30-60min）。若用户机器性能差可改 `small`（244MB，15min/1h），但中文识别准确率下降。**请用户确认或告知机器配置**。

2. **ffmpeg 安装责任**：v2 假设用户自行安装 ffmpeg 并加 PATH。是否需要后端启动时自动检测 + 提供一键安装脚本？

3. **Whisper 任务排队上限**：v2 默认队列长度无限制（任务堆积串行处理）。是否需要设上限（如 5 个，超出拒绝入队）？

4. **Whisper 失败后是否自动降级到小模型重试**：v2 默认不自动重试（admin 手动触发）。是否需要"medium 失败自动切 small 重试一次"？

5. **JWT refresh token**：v2 不做 refresh token（1h 过期重新登录）。学习场景 1h 可能不够，是否改为 4h 或加 refresh？

6. **PPT 文本提取 warning 显示位置**：v2 在管理台素材列表的 error_message 字段显示。是否需要在学习端播放页也提示用户"该课件排版信息已丢失"？

确认后即可进入开发。
