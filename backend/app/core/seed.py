"""数据库初始化与 seed 预置账号。"""

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, engine
from app.core.security import hash_password
from app.models.models import User


def create_tables() -> None:
    """建表（若不存在）。"""
    Base.metadata.create_all(bind=engine)


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


def init_db() -> None:
    """应用启动入口：建表 + seed。"""
    create_tables()
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        seed_users(db)
    finally:
        db.close()
