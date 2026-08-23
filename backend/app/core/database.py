"""数据库连接与会话管理（SQLite + SQLAlchemy 2.x）。"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import PROJECT_ROOT

# 数据库文件固定在项目根目录 app.db（避免相对路径随启动目录漂移）
_DB_PATH = PROJECT_ROOT / "app.db"
DATABASE_URL = f"sqlite:///{_DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite 允许跨线程（FastAPI 多线程）
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def get_db():
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
