"""模块 3.3/3.4 SSE 代理 + 会话管理测试。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router as chat_router
from app.api.admin_model_configs import get_default_config
from app.core.database import get_db
from app.core.security import create_access_token, encrypt_api_key, hash_password
from app.models.models import ChatSession, ModelConfig, User


@pytest.fixture()
def client(db_session, monkeypatch):
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    user = User(username="user25", password_hash=hash_password("123456"), role="user")
    db_session.add_all([admin, user])
    db_session.commit()

    cfg = ModelConfig(
        name="qwen",
        base_url="https://x",
        api_key_encrypted=encrypt_api_key("sk-test"),
        model_name="qwen-plus",
        is_default=True,
    )
    db_session.add(cfg)
    db_session.commit()

    # mock stream_chat
    async def fake_stream_chat(base_url, api_key, model_name, messages):
        for delta in ["你好", "，", "世界"]:
            yield delta

    from app.api import chat

    monkeypatch.setattr(chat, "stream_chat", fake_stream_chat)

    def _get_db_override():
        yield db_session

    app = FastAPI()
    app.include_router(chat_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(2, 'user25', 'user')}"}


def _parse_sse(resp):
    events = []
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


def test_chat_stream_flow(client):
    resp = client.post("/api/chat/stream", json={"user_question": "什么是RAG"}, headers=_user_h())
    assert resp.status_code == 200
    events = _parse_sse(resp)
    deltas = "".join(e.get("delta", "") for e in events)
    assert deltas == "你好，世界"
    assert any("session_id" in e for e in events)
    assert any(e.get("done") is True for e in events)


def test_chat_requires_auth(client):
    assert client.post("/api/chat/stream", json={"user_question": "x"}).status_code == 401


def test_chat_no_config(client, db_session):
    # 删除默认配置 → 返回"未配置大模型"
    db_session.query(ModelConfig).delete()
    db_session.commit()
    resp = client.post("/api/chat/stream", json={"user_question": "x"}, headers=_user_h())
    events = _parse_sse(resp)
    assert any("未配置大模型" in e.get("error", "") for e in events)


def test_chat_token_reject(client, monkeypatch):
    # mock build_context 返回空 messages → 拒绝
    from app.api import chat

    monkeypatch.setattr(chat, "build_context", lambda **kw: ([], "上下文超限"))
    resp = client.post("/api/chat/stream", json={"user_question": "x"}, headers=_user_h())
    events = _parse_sse(resp)
    assert any("上下文超限" in e.get("error", "") for e in events)


def test_session_persistence(client, db_session):
    resp = client.post("/api/chat/stream", json={"user_question": "q1"}, headers=_user_h())
    sid = [e["session_id"] for e in _parse_sse(resp) if "session_id" in e][0]
    # 会话已落库，含 1 轮历史
    session = db_session.query(ChatSession).filter(ChatSession.session_id == sid).first()
    assert session is not None
    msgs = json.loads(session.messages_json)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"


def test_clear_session(client, db_session):
    resp = client.post("/api/chat/stream", json={"user_question": "q1"}, headers=_user_h())
    sid = [e["session_id"] for e in _parse_sse(resp) if "session_id" in e][0]
    r = client.post(f"/api/chat/sessions/{sid}/clear", headers=_user_h())
    assert r.status_code == 200
    session = db_session.query(ChatSession).filter(ChatSession.session_id == sid).first()
    assert json.loads(session.messages_json) == []


def test_session_owner_check(client):
    # 用 admin 的会话 id，user 无法 clear
    resp = client.post(
        "/api/chat/stream",
        json={"user_question": "q"},
        headers={"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"},
    )
    sid = [e["session_id"] for e in _parse_sse(resp) if "session_id" in e][0]
    r = client.post(f"/api/chat/sessions/{sid}/clear", headers=_user_h())
    assert r.status_code == 403


def test_list_sessions(client):
    client.post("/api/chat/stream", json={"user_question": "q1"}, headers=_user_h())
    resp = client.get("/api/chat/sessions", headers=_user_h())
    assert resp.status_code == 200
    assert len(resp.json()) == 1
