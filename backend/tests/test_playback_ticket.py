"""课程绑定播放凭证与 Range 请求测试。"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.materials import router
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import Material, User


@pytest.fixture()
def client(db_session, tmp_path):
    user = User(username="user25", password_hash=hash_password("123456"), role="user")
    db_session.add(user)
    db_session.flush()
    for course_id in ("c1", "c2"):
        path = tmp_path / f"{course_id}.mp4"
        path.write_bytes(b"0123456789abcdef")
        db_session.add(
            Material(
                course_id=course_id,
                dir_path=str(tmp_path),
                video_path=str(path),
                status="ready",
            )
        )
    db_session.commit()

    def override_db():
        yield db_session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _headers():
    return {"Authorization": f"Bearer {create_access_token(1, 'user25', 'user')}"}


def test_ticketed_video_supports_range(client):
    ticket_response = client.post("/api/materials/c1/playback-ticket", headers=_headers())
    assert ticket_response.status_code == 200
    url = ticket_response.json()["url"]
    response = client.get(url, headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes 2-5/16"
    assert response.headers["content-length"] == "4"


def test_ticket_is_bound_to_course_and_requires_auth_to_issue(client):
    assert client.post("/api/materials/c1/playback-ticket").status_code == 401
    url = client.post("/api/materials/c1/playback-ticket", headers=_headers()).json()["url"]
    assert client.get(url.replace("/c1/", "/c2/")).status_code == 401


def test_expired_or_wrong_purpose_ticket_is_rejected(client):
    expired = jwt.encode(
        {
            "sub": "1",
            "course_id": "c1",
            "purpose": "media_playback",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    access_token = create_access_token(1, "user25", "user")
    assert client.get(f"/api/materials/c1/video-playback?ticket={expired}").status_code == 401
    assert client.get(f"/api/materials/c1/video-playback?ticket={access_token}").status_code == 401


def test_ticket_cannot_outlive_its_user(client, db_session):
    url = client.post("/api/materials/c1/playback-ticket", headers=_headers()).json()["url"]
    db_session.query(User).filter(User.id == 1).delete()
    db_session.commit()
    response = client.get(url)
    assert response.status_code == 401
    assert response.json()["detail"] == "用户不存在"
