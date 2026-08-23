"""模块 7.1 端到端集成测试：完整主链路（登录→上传→扫描→列表→AI流式）。

用 TestClient 挂载完整 app，覆盖真实 HTTP 语义（等同前后端联调）。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, encrypt_api_key, hash_password
from app.main import app
from app.models.models import ModelConfig, User
from app.services import storage


@pytest.fixture()
def e2e(db_session, tmp_path, monkeypatch):
    """挂载完整 app，隔离数据库与素材目录，mock 大模型。"""
    # 隔离素材目录
    monkeypatch.setattr(storage, "_materials_root", lambda: tmp_path)

    # seed 用户
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    user = User(username="user25", password_hash=hash_password("123456"), role="user")
    db_session.add_all([admin, user])
    db_session.commit()

    # 默认模型配置
    cfg = ModelConfig(
        name="qwen",
        base_url="https://x",
        api_key_encrypted=encrypt_api_key("sk-test"),
        model_name="qwen-plus",
        is_default=True,
    )
    db_session.add(cfg)
    db_session.commit()

    # mock 大模型流式
    async def fake_stream_chat(base_url, api_key, model_name, messages):
        yield "这是"

        yield "回答"

    from app.api import chat

    monkeypatch.setattr(chat, "stream_chat", fake_stream_chat)

    # 覆盖 get_db
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _admin():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def _user():
    return {"Authorization": f"Bearer {create_access_token(2, 'user25', 'user')}"}


def _parse_sse(resp):
    return [json.loads(l[5:]) for l in resp.text.splitlines() if l.startswith("data:")]


def test_full_main_chain(e2e):
    # 1. admin 登录
    r = e2e.post("/api/auth/login", json={"username": "admin", "password": "123456"})
    assert r.status_code == 200

    # 2. admin 上传视频 + 字幕 + 课件
    r = e2e.post(
        "/api/admin/materials/upload",
        params={"course_id": "demo", "file_type": "video"},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftypmp42 rest", "video/mp4")},
        headers=_admin(),
    )
    assert r.status_code == 200

    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nRAG 是检索增强生成\n"
    r = e2e.post(
        "/api/admin/materials/upload",
        params={"course_id": "demo", "file_type": "subtitle"},
        files={"file": ("s.vtt", vtt.encode(), "text/vtt")},
        headers=_admin(),
    )
    assert r.status_code == 200

    md = "# 第一章\nRAG 结合检索与生成\n# 第二章\n向量数据库\n"
    r = e2e.post(
        "/api/admin/materials/upload",
        params={"course_id": "demo", "file_type": "courseware"},
        files={"file": ("c.md", md.encode(), "text/markdown")},
        headers=_admin(),
    )
    assert r.status_code == 200

    # 3. user 登录并看课程列表（ready 可见）
    r = e2e.post("/api/auth/login", json={"username": "user25", "password": "123456"})
    assert r.status_code == 200
    r = e2e.get("/api/materials", headers=_user())
    assert r.status_code == 200
    courses = [c["course_id"] for c in r.json()]
    assert "demo" in courses

    # 4. user 选中字幕提问（SSE 流式）
    r = e2e.post(
        "/api/chat/stream",
        json={
            "course_id": "demo",
            "selected_subtitle": "RAG 是检索增强生成",
            "start_time": 1.0,
            "end_time": 5.0,
            "user_question": "什么是 RAG？",
        },
        headers=_user(),
    )
    assert r.status_code == 200
    events = _parse_sse(r)
    deltas = "".join(e.get("delta", "") for e in events)
    assert deltas == "这是回答"
    assert any(e.get("done") is True for e in events)


def test_role_isolation(e2e):
    """user 访问 /admin 接口被 403，访问管理台用户列表 403。"""
    r = e2e.get("/api/admin/users", headers=_user())
    assert r.status_code == 403


def test_error_course_hidden_from_user(e2e):
    """status=error 的课程不展示给 user。"""
    # admin 上传只有课件的课程（无视频 → error）
    e2e.post(
        "/api/admin/materials/upload",
        params={"course_id": "broken", "file_type": "courseware"},
        files={"file": ("c.md", "# 标题".encode(), "text/markdown")},
        headers=_admin(),
    )
    # user 看不到 broken
    r = e2e.get("/api/materials", headers=_user())
    ids = [c["course_id"] for c in r.json()]
    assert "broken" not in ids
    # admin 能看到 broken
    r = e2e.get("/api/materials", headers=_admin())
    ids = [c["course_id"] for c in r.json()]
    assert "broken" in ids
