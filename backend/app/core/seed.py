"""数据库初始化与首次启动 seed。"""

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, engine
from app.core.migrations import run_migrations
from app.core.security import encrypt_api_key, hash_password
from app.models.models import Material, ModelConfig, SystemSetting, User

logger = logging.getLogger(__name__)


MODEL_CONFIG_IMPORT_KEY = "llm_env_import_v1"
MODEL_CONFIG_IMPORT_NAME = "环境变量默认模型"
_API_KEY_PLACEHOLDERS = {"your-api-key-here"}


def create_tables() -> None:
    """建表（若不存在）+ 幂等补列。

    `create_all()` 只建新表、不补列，所以新增字段必须走 `run_migrations()`
    （见 app/core/migrations.py）。两者顺序不能颠倒。
    """
    Base.metadata.create_all(bind=engine)
    applied = run_migrations(engine)
    if applied:
        logger.info("启动迁移已执行: %s", ", ".join(applied))


def seed_users(db: Session) -> None:
    """首次启动预置 admin 与 user 两个账号（已存在则跳过）。"""
    defaults = [
        (settings.admin_username, settings.admin_password, "admin"),
        (settings.user_username, settings.user_password, "user"),
    ]
    for username, password, role in defaults:
        if not username or not password:
            continue
        existing = db.query(User).filter(User.username == username).first()
        if existing is None:
            db.add(User(username=username, password_hash=hash_password(password), role=role))
    db.commit()


def seed_model_config(db: Session) -> None:
    """一次性将有效的环境变量大模型配置迁移到数据库。"""
    marker = db.get(SystemSetting, MODEL_CONFIG_IMPORT_KEY)
    if marker is not None:
        return

    existing = db.query(ModelConfig).order_by(ModelConfig.id.asc()).first()
    if existing is not None:
        db.add(SystemSetting(key=MODEL_CONFIG_IMPORT_KEY, value="skipped_existing"))
        db.commit()
        return

    api_key = settings.llm_api_key.strip()
    base_url = settings.llm_base_url.strip()
    model_name = settings.llm_model_name.strip()
    if not api_key or api_key.casefold() in _API_KEY_PLACEHOLDERS or not base_url or not model_name:
        return

    db.add(
        ModelConfig(
            name=MODEL_CONFIG_IMPORT_NAME,
            base_url=base_url,
            api_key_encrypted=encrypt_api_key(api_key),
            model_name=model_name,
            is_default=True,
        )
    )
    db.add(SystemSetting(key=MODEL_CONFIG_IMPORT_KEY, value="imported"))
    db.commit()


def _reconcile_orphan_subtitle_tasks(db: Session) -> None:
    """把启动时残留的 `generating` 复位为 `pending`（v8 §5.5A.3 配套）。

    进程重启后内存队列 `_queue` 是空的，但 DB 里若残留 `generating`，这些行永远不会
    被任何 worker 推进 —— 前端会一直显示"生成中"并空轮询。

    **不自动重新 enqueue**：启动即满载转写会拖慢首个请求、吃掉 CPU/内存，
    交给管理员在素材管理页用批量操作显式触发更可控。

    ⚠️ 这个函数在 v8 之前没有存在意义：那时 DB 根本到不了 generating
    （见 admin_materials 的 root-cause 修复），自然不会有残留。修好根因后它才成为必需。
    """
    rows = db.query(Material).filter(Material.subtitle_status == "generating").all()
    if not rows:
        return
    for material in rows:
        material.subtitle_status = "pending"
        material.subtitle_error = None
        # B12：不要无条件把 source 写成 whisper。
        # 这里是"进程重启打断了生成"的复位，不是"whisper 刚生成成功"——
        # 一条人工上传的字幕被重启复位后来源就变成 whisper，PRD AC-14 直接失守。
        # 只在来源缺失时兜底推断（legacy fallback）。
        if material.subtitle_source is None and material.video_path:
            material.subtitle_source = "whisper"
    db.commit()
    logger.info("启动复位 %d 条孤儿 generating 字幕任务（进程重启导致）", len(rows))


def init_db() -> None:
    """应用启动入口：建表 + seed 账号与默认模型配置 + 复位孤儿字幕任务。"""
    create_tables()
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        seed_users(db)
        seed_model_config(db)
        _reconcile_orphan_subtitle_tasks(db)
        # P0 先使用一个稳定 project_key；现有和新课程均通过关联表绑定。
        from app.services.project_context import bind_all_materials

        bind_all_materials(db)
    finally:
        db.close()
