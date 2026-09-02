"""模块 0.3 数据库初始化测试（独立临时库，不污染 app.db）。"""

import pytest

from sqlalchemy import inspect

from app.core.security import decrypt_api_key, encrypt_api_key, hash_password, verify_password
from app.core.seed import MODEL_CONFIG_IMPORT_KEY, seed_model_config, seed_users
from app.models.models import ModelConfig, SystemSetting, User


def test_create_tables(db_session):
    inspector = inspect(db_session.get_bind())
    tables = set(inspector.get_table_names())
    assert {"users", "model_configs", "model_routes", "materials", "chat_sessions", "system_settings"} <= tables


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


def test_seed_model_config_imports_once(db_session, monkeypatch):
    from app.core.seed import settings

    monkeypatch.setattr(settings, "llm_base_url", " https://example.test/v1 ")
    monkeypatch.setattr(settings, "llm_api_key", " secret-test-1234 ")
    monkeypatch.setattr(settings, "llm_model_name", " model-test ")

    seed_model_config(db_session)
    seed_model_config(db_session)

    configs = db_session.query(ModelConfig).order_by(ModelConfig.id.asc()).all()
    assert len(configs) == 1
    assert configs[0].name == "环境变量默认模型"
    assert configs[0].base_url == "https://example.test/v1"
    assert configs[0].model_name == "model-test"
    assert configs[0].is_default is True
    assert configs[0].api_key_encrypted != "secret-test-1234"
    assert decrypt_api_key(configs[0].api_key_encrypted) == "secret-test-1234"
    assert db_session.get(SystemSetting, MODEL_CONFIG_IMPORT_KEY).value == "imported"

    db_session.delete(configs[0])
    db_session.commit()
    seed_model_config(db_session)
    assert db_session.query(ModelConfig).count() == 0


def test_seed_model_config_skips_existing_and_never_reimports(db_session, monkeypatch):
    from app.core.seed import settings

    monkeypatch.setattr(settings, "llm_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "llm_api_key", "secret-test-1234")
    monkeypatch.setattr(settings, "llm_model_name", "model-test")
    db_session.add(
        ModelConfig(
            name="手工配置",
            base_url="https://manual.test/v1",
            api_key_encrypted=encrypt_api_key("manual-key"),
            model_name="manual-model",
            is_default=True,
        )
    )
    db_session.commit()

    seed_model_config(db_session)
    assert db_session.query(ModelConfig).count() == 1
    assert db_session.get(SystemSetting, MODEL_CONFIG_IMPORT_KEY).value == "skipped_existing"

    db_session.query(ModelConfig).delete()
    db_session.commit()
    seed_model_config(db_session)
    assert db_session.query(ModelConfig).count() == 0


@pytest.mark.parametrize("api_key", ["", "   ", "your-api-key-here", " YOUR-API-KEY-HERE "])
def test_seed_model_config_ignores_missing_or_placeholder_key(db_session, monkeypatch, api_key):
    from app.core.seed import settings

    monkeypatch.setattr(settings, "llm_base_url", "https://example.test/v1")
    monkeypatch.setattr(settings, "llm_api_key", api_key)
    monkeypatch.setattr(settings, "llm_model_name", "model-test")

    seed_model_config(db_session)

    assert db_session.query(ModelConfig).count() == 0
    assert db_session.get(SystemSetting, MODEL_CONFIG_IMPORT_KEY) is None
