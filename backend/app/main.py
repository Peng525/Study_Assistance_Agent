"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.seed import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表 + seed 预置账号。"""
    init_db()
    yield


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

# CORS 配置（从 settings 读取，本地开发前后端同源代理）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from app.api import (  # noqa: E402
    admin_dashboard,
    admin_materials,
    admin_model_configs,
    admin_project_context,
    admin_users,
    auth,
    chat,
    materials,
)

app.include_router(auth.router)
app.include_router(admin_users.router)
app.include_router(admin_materials.router)
app.include_router(admin_model_configs.router)
app.include_router(admin_dashboard.router)
app.include_router(admin_project_context.router)
app.include_router(chat.router)
app.include_router(materials.router)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """健康检查接口。"""
    return {"status": "ok", "app": settings.app_name}
