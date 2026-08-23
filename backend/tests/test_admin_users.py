"""模块 1.4 admin 重置密码 + 用户列表测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_users import router as admin_users_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
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
    app.include_router(admin_users_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _admin_headers():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def _user_headers():
    return {"Authorization": f"Bearer {create_access_token(2, 'user25', 'user')}"}


def test_list_users_requires_admin(client):
    assert client.get("/api/admin/users").status_code == 401  # 无 token
    assert client.get("/api/admin/users", headers=_user_headers()).status_code == 403


def test_list_users_ok(client):
    resp = client.get("/api/admin/users", headers=_admin_headers())
    assert resp.status_code == 200
    usernames = [u["username"] for u in resp.json()]
    assert "admin" in usernames and "user25" in usernames


def test_reset_password_success(client, db_session):
    resp = client.post("/api/admin/users/2/reset-password", headers=_admin_headers())
    assert resp.status_code == 200
    user = db_session.query(User).filter(User.id == 2).first()
    assert verify_password("123456", user.password_hash)


def test_reset_password_self_lock(client):
    resp = client.post("/api/admin/users/1/reset-password", headers=_admin_headers())
    assert resp.status_code == 400
    assert "不能重置自己" in resp.json()["detail"]


def test_reset_password_requires_admin(client):
    resp = client.post("/api/admin/users/2/reset-password", headers=_user_headers())
    assert resp.status_code == 403


def test_reset_password_not_found(client):
    resp = client.post("/api/admin/users/999/reset-password", headers=_admin_headers())
    assert resp.status_code == 404
