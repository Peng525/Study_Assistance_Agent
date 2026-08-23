"""模块 1.2/1.3 登录登出/改密测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.core.database import get_db
from app.core.security import hash_password
from app.models.models import User


@pytest.fixture()
def client(db_session):
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    db_session.add(admin)
    db_session.commit()

    def _get_db_override():
        yield db_session

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _login(client, username="admin", password="123456"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_login_success(client):
    resp = _login(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"]
    assert data["user"]["username"] == "admin"
    assert data["user"]["role"] == "admin"


def test_login_wrong_password(client):
    resp = _login(client, password="wrong")
    assert resp.status_code == 401
    assert "用户名或密码错误" in resp.json()["detail"]


def test_login_unknown_user(client):
    resp = _login(client, username="nobody")
    assert resp.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_current_user(client):
    token = _login(client).json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_logout_ok(client):
    token = _login(client).json()["access_token"]
    resp = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_change_password_success(client):
    token = _login(client).json()["access_token"]
    resp = client.post(
        "/api/auth/change-password",
        json={"old_password": "123456", "new_password": "newpass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    # 新密码可登录
    assert _login(client, password="newpass123").status_code == 200


def test_change_password_wrong_old(client):
    token = _login(client).json()["access_token"]
    resp = client.post(
        "/api/auth/change-password",
        json={"old_password": "bad", "new_password": "newpass123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "旧密码错误" in resp.json()["detail"]


def test_change_password_too_short(client):
    token = _login(client).json()["access_token"]
    resp = client.post(
        "/api/auth/change-password",
        json={"old_password": "123456", "new_password": "123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422  # pydantic 校验拦截
