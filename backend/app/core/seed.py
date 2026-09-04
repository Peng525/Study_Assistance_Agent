"""数据库初始化与首次启动 seed。"""

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, engine
from app.core.migrations import run_migrations
from app.core.security import encrypt_api_key, hash_password
from app.models.models import ModelConfig, SystemSetting, User

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


def init_db() -> None:
    """应用启动入口：建表 + seed 账号与默认模型配置。"""
    create_tables()
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        seed_users(db)
        seed_model_config(db)
        # P0 先使用一个稳定 project_key；现有和新课程均通过关联表绑定。
        from app.services.project_context import bind_all_materials

        bind_all_materials(db)
    finally:
        db.close()
