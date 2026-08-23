"""模块 admin_dashboard 统计接口测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_dashboard import router as stats_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import ModelConfig, User


@pytest.fixture()
def client(db_session):
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    db_session.add(admin)
    db_session.commit()
    cfg = ModelConfig(
        name="qwen",
        base_url="https://x",
        api_key_encrypted="x",
        model_name="qwen-plus",
        is_default=True,
    )
    db_session.add(cfg)
    db_session.commit()

    def _get_db_override():
        yield db_session

    app = FastAPI()
    app.include_router(stats_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _h():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def test_stats_returns_default_model(client):
    resp = client.get("/api/admin/stats", headers=_h())
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_model_name"] == "qwen-plus"
    assert data["material_total"] == 0
    # 连续 7 天数据
    assert len(data["last_7_days_sessions"]) == 7


def test_stats_requires_admin(client):
    assert client.get("/api/admin/stats").status_code == 401
