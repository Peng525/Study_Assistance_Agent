"""模块 0.3 数据库初始化测试（独立临时库，不污染 app.db）。"""

from sqlalchemy import inspect

from app.core.security import hash_password, verify_password
from app.core.seed import seed_users
from app.models.models import User


def test_create_tables(db_session):
    inspector = inspect(db_session.get_bind())
    tables = set(inspector.get_table_names())
    assert {"users", "model_configs", "materials", "chat_sessions", "system_settings"} <= tables


def test_seed_users(db_session):
    seed_users(db_session)

    admin = db_session.query(User).filter(User.username == "admin").first()
    user = db_session.query(User).filter(User.username == "user25").first()

    assert admin is not None
    assert admin.role == "admin"
    assert verify_password("123456", admin.password_hash)

    assert user is not None
    assert user.role == "user"
    assert verify_password("123456", user.password_hash)

    # 重复 seed 不重复创建
    seed_users(db_session)
    assert db_session.query(User).filter(User.username == "admin").count() == 1


def test_password_hash_roundtrip():
    h = hash_password("123456")
    assert h != "123456"
    assert verify_password("123456", h)
    assert not verify_password("wrong", h)
