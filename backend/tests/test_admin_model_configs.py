"""模块 3.1 模型配置 CRUD 测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_model_configs import router as mc_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import User


@pytest.fixture()
def client(db_session):
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    user = User(username="user25", password_hash=hash_password("123456"), role="user")
    db_session.add_all([admin, user])
    db_session.commit()

    def _get_db_override():
        yield db_session

    app = FastAPI()
    app.include_router(mc_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _h():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def test_create_and_list(client):
    resp = client.post(
        "/api/admin/model-configs",
        json={"name": "qwen", "base_url": "https://x", "api_key": "sk-test1234", "model_name": "qwen-plus", "is_default": True},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json()["api_key_masked"] == "sk-****1234"

    lst = client.get("/api/admin/model-configs", headers=_h()).json()
    assert len(lst) == 1
    assert lst[0]["is_default"] is True


def test_create_empty_key_rejected(client):
    resp = client.post(
        "/api/admin/model-configs",
        json={"name": "x", "base_url": "https://x", "api_key": "", "model_name": "m", "is_default": False},
        headers=_h(),
    )
    assert resp.status_code == 400


def test_default_unique(client):
    for i in range(2):
        client.post(
            "/api/admin/model-configs",
            json={"name": f"c{i}", "base_url": "https://x", "api_key": f"sk-key{i}", "model_name": "m", "is_default": True},
            headers=_h(),
        )
    lst = client.get("/api/admin/model-configs", headers=_h()).json()
    defaults = [c for c in lst if c["is_default"]]
    assert len(defaults) == 1


def test_update_keep_key_when_empty(client):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "qwen", "base_url": "https://x", "api_key": "sk-orig9999", "model_name": "qwen-plus", "is_default": False},
        headers=_h(),
    ).json()["id"]
    # 编辑时 api_key 留空 → 不修改
    resp = client.put(
        f"/api/admin/model-configs/{cid}",
        json={"name": "qwen2", "base_url": "https://y", "api_key": "", "model_name": "qwen-max", "is_default": False},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json()["api_key_masked"] == "sk-****9999"  # 原 key 保留
    assert resp.json()["model_name"] == "qwen-max"


def test_update_not_found(client):
    resp = client.put(
        "/api/admin/model-configs/999",
        json={"name": "x", "base_url": "https://x", "api_key": "", "model_name": "m", "is_default": False},
        headers=_h(),
    )
    assert resp.status_code == 404


def test_delete(client):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "qwen", "base_url": "https://x", "api_key": "sk-test1234", "model_name": "qwen-plus", "is_default": False},
        headers=_h(),
    ).json()["id"]
    assert client.delete(f"/api/admin/model-configs/{cid}", headers=_h()).status_code == 200
    assert client.get("/api/admin/model-configs", headers=_h()).json() == []


def test_requires_admin(client):
    token = create_access_token(2, "user25", "user")
    resp = client.get("/api/admin/model-configs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
