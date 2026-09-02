"""模块 3.3/3.4 SSE 代理 + 会话管理测试。"""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.chat import _append_turn_atomic, router as chat_router
from app.api.admin_model_configs import get_default_config
from app.core.database import get_db
from app.core.security import create_access_token, encrypt_api_key, hash_password
from app.models.models import (
    ChatContextBinding,
    ChatSession,
    Material,
    ModelConfig,
    ModelRoute,
    ProjectContextVersion,
    ProjectSource,
    User,
    VideoKnowledge,
)
from app.services.llm_client import LLMError
from app.services.llm_errors import classify_provider_error


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


def test_chat_stream_uses_its_own_database_session(client, db_session, monkeypatch):
    from app.api import chat

    async def capture_session(stream_db, config, api_key, messages, outcome, stream_fn):
        assert stream_db is not db_session
        outcome.success = True
        outcome.answer = "独立会话"
        outcome.model_name = config.model_name
        yield {"type": "delta", "delta": "独立会话"}
        yield {"type": "done", "done": True, "model_name": config.model_name}

    monkeypatch.setattr(chat, "stream_model_chain", capture_session)
    response = client.post(
        "/api/chat/stream",
        json={"user_question": "测试流式数据库会话"},
        headers=_user_h(),
    )

    assert response.status_code == 200
    assert any(event.get("done") is True for event in _parse_sse(response))


def test_chat_ignores_requested_non_default_api(client, db_session, monkeypatch):
    default = get_default_config(db_session)
    other = ModelConfig(
        name="other-api",
        base_url="https://other.example/v1",
        api_key_encrypted=encrypt_api_key("other-key"),
        model_name="other-model",
        is_default=False,
    )
    db_session.add(other)
    db_session.commit()
    calls = []

    async def capture_stream(base_url, api_key, model_name, messages):
        calls.append((base_url, api_key, model_name))
        yield "default"

    from app.api import chat

    monkeypatch.setattr(chat, "stream_chat", capture_stream)
    response = client.post(
        "/api/chat/stream",
        json={"user_question": "q", "model_config_id": other.id},
        headers=_user_h(),
    )
    assert response.status_code == 200
    assert calls == [(default.base_url, "sk-test", default.model_name)]


def test_switching_default_api_changes_new_chat_provider(client, db_session, monkeypatch):
    original = get_default_config(db_session)
    other = ModelConfig(
        name="deepseek",
        base_url="https://deepseek.example/v1",
        api_key_encrypted=encrypt_api_key("deepseek-key"),
        model_name="deepseek-flash",
        is_default=True,
    )
    original.is_default = False
    db_session.add_all([original, other])
    db_session.commit()
    calls = []

    async def capture_stream(base_url, api_key, model_name, messages):
        calls.append((base_url, api_key, model_name))
        yield "switched"

    from app.api import chat

    monkeypatch.setattr(chat, "stream_chat", capture_stream)
    response = client.post("/api/chat/stream", json={"user_question": "q"}, headers=_user_h())
    assert response.status_code == 200
    assert calls == [("https://deepseek.example/v1", "deepseek-key", "deepseek-flash")]


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


def test_concurrent_session_writes_merge_both_rounds(client, db_session):
    cfg = get_default_config(db_session)
    config_id = cfg.id
    session = ChatSession(
        session_id="concurrent-session",
        user_id=2,
        messages_json="[]",
        model_config_id=config_id,
    )
    db_session.add(session)
    db_session.commit()
    SessionFactory = sessionmaker(bind=db_session.get_bind())
    barrier = Barrier(2)

    def write_turn(index: int):
        worker_db = SessionFactory()
        try:
            barrier.wait()
            _append_turn_atomic(
                worker_db,
                "concurrent-session",
                {"role": "user", "content": f"q{index}"},
                {"role": "assistant", "content": f"a{index}"},
                config_id,
            )
        finally:
            worker_db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write_turn, (1, 2)))

    db_session.expire_all()
    stored = db_session.query(ChatSession).filter(
        ChatSession.session_id == "concurrent-session"
    ).one()
    contents = {item["content"] for item in json.loads(stored.messages_json)}
    assert contents == {"q1", "a1", "q2", "a2"}


def test_unbound_session_binds_first_course_and_rejects_cross_course(client, db_session):
    db_session.add_all(
        [
            Material(course_id="course-a", dir_path="materials/a", status="ready"),
            Material(course_id="course-b", dir_path="materials/b", status="ready"),
        ]
    )
    db_session.commit()
    first = client.post("/api/chat/stream", json={"user_question": "先问通用问题"}, headers=_user_h())
    sid = [event["session_id"] for event in _parse_sse(first) if "session_id" in event][0]

    bound = client.post(
        "/api/chat/stream",
        json={"session_id": sid, "course_id": "course-a", "user_question": "课程 A"},
        headers=_user_h(),
    )
    assert bound.status_code == 200
    db_session.expire_all()
    assert db_session.query(ChatSession).filter(ChatSession.session_id == sid).one().course_id == "course-a"

    rejected = client.post(
        "/api/chat/stream",
        json={"session_id": sid, "course_id": "course-b", "user_question": "课程 B"},
        headers=_user_h(),
    )
    assert rejected.status_code == 409


def test_chat_uses_published_project_context_without_subtitle(client, db_session, monkeypatch):
    from app.api import chat
    from app.services.project_context import (
        bind_material,
        ensure_default_project,
        manifest_json,
        snapshot_chunks,
    )

    material = Material(
        course_id="spring-intro",
        dir_path="materials/spring-intro",
        video_original_filename="Spring 介绍.mp4",
        status="ready",
    )
    db_session.add(material)
    db_session.flush()
    project = ensure_default_project(db_session)
    bind_material(db_session, material, project)
    source = ProjectSource(
        project_id=project.id,
        original_filename="project.md",
        source_format="md",
        file_path="project.md",
        text_cached="# 私有约束\n项目内部代号 Aurora-17，只允许本地部署。",
        source_hash="a" * 64,
        status="active",
    )
    db_session.add(source)
    db_session.flush()
    version = ProjectContextVersion(
        project_id=project.id,
        version=1,
        summary_text="项目代号 Aurora-17，采用本地部署。",
        source_manifest_json=manifest_json([source]),
        status="published",
    )
    db_session.add(version)
    db_session.flush()
    snapshot_chunks(db_session, version, [source])
    db_session.commit()

    captured = []

    async def capture_stream(base_url, api_key, model_name, messages):
        captured.extend(messages)
        yield "项目回答"

    monkeypatch.setattr(chat, "stream_chat", capture_stream)
    original_get_default = chat.get_default_config
    published_v2 = False

    def publish_v2_between_context_and_session(db):
        nonlocal published_v2
        if not published_v2:
            published_v2 = True
            version.status = "superseded"
            source_v2 = ProjectSource(
                project_id=project.id,
                original_filename="project-v2.md",
                source_format="md",
                file_path="project-v2.md",
                text_cached="# 新版本\n项目改为云端部署。",
                source_hash="b" * 64,
                status="active",
            )
            db.add(source_v2)
            db.flush()
            version_v2 = ProjectContextVersion(
                project_id=project.id,
                version=2,
                summary_text="项目采用云端部署。",
                source_manifest_json=manifest_json([source, source_v2]),
                status="published",
            )
            db.add(version_v2)
            db.flush()
            snapshot_chunks(db, version_v2, [source, source_v2])
        return original_get_default(db)

    # 在首轮 messages 已使用 v1 后发布 v2，验证持久 binding 不会重新追 latest。
    monkeypatch.setattr(chat, "get_default_config", publish_v2_between_context_and_session)
    response = client.post(
        "/api/chat/stream",
        json={
            "course_id": "spring-intro",
            "start_time": 75.5,
            "video_duration": 600,
            "user_question": "这个项目如何部署？",
        },
        headers=_user_h(),
    )
    assert response.status_code == 200
    prompt = "\n".join(message["content"] for message in captured)
    assert "Aurora-17" in prompt
    assert "当前播放位置：75.5 秒" in prompt
    assert "不能据此推断声音或画面" in prompt

    sid = [event["session_id"] for event in _parse_sse(response) if "session_id" in event][0]
    binding = db_session.query(ChatContextBinding).filter(ChatContextBinding.session_id == sid).one()
    assert binding.context_version_id == version.id
    session = db_session.query(ChatSession).filter(ChatSession.session_id == sid).one()
    assistant = json.loads(session.messages_json)[-1]
    assert assistant["context_meta"]["project_context_version"] == 1
    assert assistant["context_meta"]["start_time"] == 75.5
    assert assistant["context_meta"]["subtitle_context"] is False


def test_video_course_type_controls_outline_injection(client, db_session, monkeypatch):
    from app.api import chat

    theory = Material(
        course_id="theory-video",
        dir_path="materials/theory-video",
        courseware_text_cached="LEGACY_COURSEWARE_SHOULD_NOT_BE_SENT",
        status="ready",
    )
    practice = Material(
        course_id="practice-video",
        dir_path="materials/practice-video",
        status="ready",
    )
    db_session.add_all([theory, practice])
    db_session.flush()
    theory_context = VideoKnowledge(
        material_id=theory.id,
        course_type="theory",
        outline_text_cached="THEORY_OUTLINE_SHOULD_NOT_BE_SENT",
        outline_status="ready",
    )
    practice_context = VideoKnowledge(
        material_id=practice.id,
        course_type="practice",
        outline_text_cached="PRACTICE_VIDEO_ONLY_OUTLINE",
        outline_status="draft",
    )
    db_session.add_all([theory_context, practice_context])
    db_session.commit()

    captured: list[list[dict]] = []

    async def capture_stream(base_url, api_key, model_name, messages):
        captured.append(messages)
        yield "回答"

    monkeypatch.setattr(chat, "stream_chat", capture_stream)
    theory_response = client.post(
        "/api/chat/stream",
        json={"course_id": "theory-video", "user_question": "解释概念"},
        headers=_user_h(),
    )
    assert theory_response.status_code == 200
    theory_prompt = "\n".join(item["content"] for item in captured[-1])
    assert "THEORY_OUTLINE_SHOULD_NOT_BE_SENT" not in theory_prompt
    assert "LEGACY_COURSEWARE_SHOULD_NOT_BE_SENT" not in theory_prompt

    draft_response = client.post(
        "/api/chat/stream",
        json={"course_id": "practice-video", "user_question": "项目怎么做"},
        headers=_user_h(),
    )
    assert draft_response.status_code == 200
    draft_prompt = "\n".join(item["content"] for item in captured[-1])
    assert "PRACTICE_VIDEO_ONLY_OUTLINE" not in draft_prompt

    practice_context.outline_status = "ready"
    db_session.add(practice_context)
    db_session.commit()
    ready_response = client.post(
        "/api/chat/stream",
        json={"course_id": "practice-video", "user_question": "项目怎么做"},
        headers=_user_h(),
    )
    assert ready_response.status_code == 200
    ready_prompt = "\n".join(item["content"] for item in captured[-1])
    assert "【当前视频课程大纲】" in ready_prompt
    assert "PRACTICE_VIDEO_ONLY_OUTLINE" in ready_prompt
    session = db_session.query(ChatSession).order_by(ChatSession.id.desc()).first()
    assistant = json.loads(session.messages_json)[-1]
    assert assistant["context_meta"]["course_type"] == "practice"
    assert assistant["context_meta"]["video_outline_included"] is True


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


def test_fallback_clears_partial_and_persists_only_final_answer(client, db_session, monkeypatch):
    cfg = db_session.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).one()
    db_session.add_all(
        [
            ModelRoute(model_config_id=cfg.id, display_name="first", model_name="first", priority=10),
            ModelRoute(model_config_id=cfg.id, display_name="second", model_name="second", priority=20),
        ]
    )
    db_session.commit()

    async def fallback_stream(_base_url, _api_key, model_name, _messages):
        if model_name == "first":
            yield "残缺"
            raise LLMError(
                classify_provider_error(
                    403,
                    {"code": "AllocationQuota.FreeTierOnly", "message": "exhausted"},
                )
            )
        yield "最终答案"

    from app.api import chat

    monkeypatch.setattr(chat, "stream_chat", fallback_stream)
    response = client.post("/api/chat/stream", json={"user_question": "q"}, headers=_user_h())
    events = _parse_sse(response)
    assert any(event.get("attempt_reset") for event in events)
    assert any(event.get("fallback") for event in events)
    assert events[-1]["model_name"] == "second"
    session = db_session.query(ChatSession).order_by(ChatSession.id.desc()).first()
    stored = json.loads(session.messages_json)
    assert stored[-1]["content"] == "最终答案"
    assert stored[-1]["model_name"] == "second"
    assert stored[-1]["attempted_models"] == ["first", "second"]


def test_failed_new_session_is_not_persisted(client, db_session, monkeypatch):
    async def failed_stream(*_args):
        if False:
            yield ""
        raise LLMError(classify_provider_error(401, {"code": "InvalidApiKey"}))

    from app.api import chat

    monkeypatch.setattr(chat, "stream_chat", failed_stream)
    response = client.post("/api/chat/stream", json={"user_question": "q"}, headers=_user_h())
    events = _parse_sse(response)

    assert any(event.get("error") for event in events)
    assert db_session.query(ChatSession).count() == 0
