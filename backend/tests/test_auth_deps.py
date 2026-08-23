"""模块 1.1 JWT 鉴权测试。"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, require_admin
from app.core.security import create_access_token, decode_access_token


@pytest.fixture()
def client_with_auth(db_session):
    """构建一个带鉴权依赖的测试 app，覆盖 get_db。"""
    from app.core.database import get_db
    from app.models.models import User
    from app.core.security import hash_password

    # 造两个用户
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    user = User(username="user25", password_hash=hash_password("123456"), role="user")
    db_session.add_all([admin, user])
    db_session.commit()

    app = FastAPI()

    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override

    @app.get("/me")
    def me(current=Depends(get_current_user)):
        return {"username": current.username, "role": current.role}

    @app.get("/admin-only")
    def admin_only(current=Depends(require_admin)):
        return {"ok": True}

    return TestClient(app)


def test_jwt_roundtrip():
    token = create_access_token(1, "admin", "admin")
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "1"
    assert payload["username"] == "admin"
    assert payload["role"] == "admin"
    assert "exp" in payload


def test_jwt_invalid_token_returns_none():
    assert decode_access_token("invalid.token.here") is None
    assert decode_access_token("") is None


def test_me_without_token_401(client_with_auth):
    resp = client_with_auth.get("/me")
    assert resp.status_code == 401


def test_me_with_valid_token(client_with_auth):
    token = create_access_token(1, "admin", "admin")
    resp = client_with_auth.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "admin", "role": "admin"}


def test_admin_route_user_forbidden(client_with_auth):
    # user25 的 id 是 2
    token = create_access_token(2, "user25", "user")
    resp = client_with_auth.get("/admin-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_admin_route_admin_ok(client_with_auth):
    token = create_access_token(1, "admin", "admin")
    resp = client_with_auth.get("/admin-only", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
