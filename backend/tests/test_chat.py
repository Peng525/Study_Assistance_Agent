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
    ChatMessage,
    ChatContextBinding,
    ChatSession,
    ColumnChatSession,
    LLMCallLog,
    Material,
    ModelConfig,
    ModelRoute,
    ProjectContextVersion,
    ProjectSource,
    ProjectSourceOutline,
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


def test_successful_chat_audit_matches_actual_model_messages(client, db_session, monkeypatch):
    from app.api import chat

    captured = []

    async def capture_stream(base_url, api_key, model_name, messages):
        captured.extend(messages)
        yield "审计回答"

    monkeypatch.setattr(chat, "stream_chat", capture_stream)
    response = client.post(
        "/api/chat/stream",
        json={"course_id": "audit-course", "start_time": 42, "user_question": "审计问题"},
        headers=_user_h(),
    )
    assert response.status_code == 200
    record = db_session.query(LLMCallLog).one()
    assert record.user_id == 2
    assert record.username_snapshot == "user25"
    assert record.course_id == "audit-course"
    assert record.start_time == 42
    assert record.status == "success"
    assert record.answer_text == "审计回答"
    assert json.loads(record.request_messages_json) == captured
    serialized = record.request_messages_json.lower()
    assert "sk-test" not in serialized
    assert "authorization" not in serialized


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
    audit = db_session.query(LLMCallLog).one()
    assert audit.status == "rejected"
    assert json.loads(audit.request_messages_json) == []


def test_chat_token_reject(client, db_session, monkeypatch):
    # mock build_context 返回空 messages → 拒绝
    from app.api import chat

    monkeypatch.setattr(chat, "build_context", lambda **kw: ([], "上下文超限"))
    resp = client.post("/api/chat/stream", json={"user_question": "x"}, headers=_user_h())
    events = _parse_sse(resp)
    assert any("上下文超限" in e.get("error", "") for e in events)
    assert db_session.query(LLMCallLog).one().status == "rejected"


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


def test_column_context_is_same_for_theory_and_practice(client, db_session, monkeypatch):
    from app.api import chat
    from app.services.project_context import ensure_default_project

    project = ensure_default_project(db_session)
    source = ProjectSource(
        project_id=project.id,
        original_filename="Spring.pptx",
        source_format="pptx",
        file_path="Spring.pptx",
        text_cached="整份课件",
        source_hash="a" * 64,
        status="active",
    )
    db_session.add(source)
    db_session.flush()
    source_outline = ProjectSourceOutline(
        source_id=source.id,
        outline_text="READY_COLUMN_OUTLINE",
        status="ready",
        source_hash=source.source_hash,
    )
    theory = Material(
        course_id="theory-video",
        dir_path="materials/theory-video",
        status="ready",
    )
    practice = Material(
        course_id="practice-video",
        dir_path="materials/practice-video",
        status="ready",
    )
    db_session.add_all([source_outline, theory, practice])
    db_session.flush()
    theory_context = VideoKnowledge(
        material_id=theory.id,
        source_id=source.id,
        course_type="theory",
        page_start=1,
        page_end=2,
        knowledge_text_cached="THEORY_PAGE_TEXT",
        outline_text_cached="THEORY_OUTLINE_SHOULD_NOT_BE_SENT",
        outline_status="ready",
    )
    practice_context = VideoKnowledge(
        material_id=practice.id,
        source_id=source.id,
        course_type="practice",
        page_start=3,
        page_end=4,
        knowledge_text_cached="PRACTICE_PAGE_TEXT",
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
    assert "【专栏总大纲】" in theory_prompt
    assert "READY_COLUMN_OUTLINE" in theory_prompt
    assert "THEORY_PAGE_TEXT" in theory_prompt

    draft_response = client.post(
        "/api/chat/stream",
        json={"course_id": "practice-video", "user_question": "项目怎么做"},
        headers=_user_h(),
    )
    assert draft_response.status_code == 200
    practice_prompt = "\n".join(item["content"] for item in captured[-1])
    assert "PRACTICE_VIDEO_ONLY_OUTLINE" not in practice_prompt
    assert "READY_COLUMN_OUTLINE" in practice_prompt
    assert "PRACTICE_PAGE_TEXT" in practice_prompt
    session = db_session.query(ChatSession).order_by(ChatSession.id.desc()).first()
    assistant = json.loads(session.messages_json)[-1]
    assert assistant["context_meta"]["course_type"] == "practice"
    assert assistant["context_meta"]["column_outline_included"] is True
    source_outline.status = "draft"
    db_session.add(source_outline)
    db_session.commit()
    client.post(
        "/api/chat/stream",
        json={"course_id": "theory-video", "user_question": "继续解释"},
        headers=_user_h(),
    )
    draft_prompt = "\n".join(item["content"] for item in captured[-1])
    assert "READY_COLUMN_OUTLINE" not in draft_prompt
    assert "THEORY_PAGE_TEXT" in draft_prompt


def test_mapped_video_without_course_text_is_rejected_before_model_call(
    client, db_session, monkeypatch
):
    from app.api import chat
    from app.services.project_context import ensure_default_project

    project = ensure_default_project(db_session)
    source = ProjectSource(
        project_id=project.id,
        original_filename="Spring.pptx",
        source_format="pptx",
        file_path="Spring.pptx",
        text_cached="整份课件",
        source_hash="b" * 64,
        status="active",
    )
    material = Material(course_id="stale-video", dir_path="materials/stale-video", status="ready")
    db_session.add_all([source, material])
    db_session.flush()
    db_session.add(VideoKnowledge(
        material_id=material.id,
        source_id=source.id,
        course_type="theory",
        page_start=1,
        page_end=2,
        knowledge_text_cached=None,
    ))
    db_session.commit()

    async def should_not_call(*args, **kwargs):
        raise AssertionError("课程文本缺失时不应调用模型")
        yield  # pragma: no cover

    monkeypatch.setattr(chat, "stream_chat", should_not_call)
    response = client.post(
        "/api/chat/stream",
        json={"course_id": "stale-video", "user_question": "当前课程讲了什么"},
        headers=_user_h(),
    )
    assert response.status_code == 200
    assert any("课程证据尚未配置" in event.get("error", "") for event in _parse_sse(response))


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
    audit = db_session.query(LLMCallLog).one()
    assert audit.status == "success"
    assert json.loads(audit.attempted_models_json) == ["first", "second"]
    assert audit.final_model_name == "second"
    assert audit.fallback_count == 1
    assert audit.answer_text == "最终答案"


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
    audit = db_session.query(LLMCallLog).one()
    assert audit.status == "failed"
    assert audit.answer_text == ""
    assert audit.error_category == "credential_auth"


def test_empty_model_chain_is_a_rejected_audit_not_an_interruption(
    client, db_session, monkeypatch
):
    cfg = db_session.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).one()
    db_session.add(
        ModelRoute(
            model_config_id=cfg.id,
            display_name="disabled",
            model_name="disabled-model",
            priority=10,
            is_enabled=False,
        )
    )
    db_session.commit()

    async def should_not_call(*_args):
        raise AssertionError("无候选模型时不应调用供应商")
        yield  # pragma: no cover

    from app.api import chat

    monkeypatch.setattr(chat, "stream_chat", should_not_call)
    response = client.post(
        "/api/chat/stream", json={"user_question": "测试空模型链"}, headers=_user_h()
    )

    assert any("当前没有可用模型" in event.get("error", "") for event in _parse_sse(response))
    audit = db_session.query(LLMCallLog).one()
    assert audit.status == "rejected"
    assert json.loads(audit.attempted_models_json) == []
    assert "当前没有可用模型" in audit.error_message


def _add_column(db_session, *, filename="Spring.pptx", courses=("video-003", "video-005")):
    from app.services.project_context import ensure_default_project

    project = ensure_default_project(db_session)
    source = ProjectSource(
        project_id=project.id,
        original_filename=filename,
        source_format="pptx",
        file_path=filename,
        text_cached="整份课件",
        source_hash=filename[0].lower() * 64,
        status="active",
    )
    db_session.add(source)
    db_session.flush()
    materials = []
    for index, course_id in enumerate(courses):
        material = Material(
            course_id=course_id,
            dir_path=f"materials/{course_id}",
            video_original_filename=f"第{index + 1}讲.mp4",
            status="ready",
        )
        db_session.add(material)
        db_session.flush()
        db_session.add(
            VideoKnowledge(
                material_id=material.id,
                source_id=source.id,
                page_start=index + 1,
                page_end=index + 2,
                knowledge_text_cached=f"第{index + 1}讲课件原文",
            )
        )
        materials.append(material)
    db_session.commit()
    return source, materials


def test_column_session_is_shared_across_videos_and_restores_complete_history(client, db_session):
    source, _ = _add_column(db_session)
    first = client.post(
        "/api/chat/stream",
        json={"course_id": "video-003", "start_time": 65, "user_question": "第一问"},
        headers=_user_h(),
    )
    first_sid = next(event["session_id"] for event in _parse_sse(first) if "session_id" in event)
    first_done = next(event for event in _parse_sse(first) if event.get("done"))
    assert first_done["thinking_ms"] >= 0
    second = client.post(
        "/api/chat/stream",
        json={"course_id": "video-005", "start_time": 125, "user_question": "第二问"},
        headers=_user_h(),
    )
    second_sid = next(event["session_id"] for event in _parse_sse(second) if "session_id" in event)
    assert second_sid == first_sid

    restored = client.get(
        "/api/chat/column-session?course_id=video-005", headers=_user_h()
    ).json()
    assert restored["session_id"] == first_sid
    assert restored["column"]["source_id"] == source.id
    assert [item["content"] for item in restored["messages"]] == [
        "第一问", "你好，世界", "第二问", "你好，世界"
    ]
    assert restored["messages"][0]["video_name"] == "第1讲.mp4"
    assert restored["messages"][0]["start_time"] == 65
    assert restored["messages"][1]["thinking_ms"] == first_done["thinking_ms"]

    cleared = client.post(f"/api/chat/sessions/{first_sid}/clear", headers=_user_h())
    assert cleared.json()["session_id"] == first_sid
    after_clear = client.get(
        "/api/chat/column-session?course_id=video-003", headers=_user_h()
    ).json()
    assert after_clear["session_id"] == first_sid
    assert after_clear["messages"] == []
    assert db_session.query(ColumnChatSession).filter_by(session_id=first_sid).one().memory_summary == ""


def test_column_sessions_are_isolated_by_column_and_user(client, db_session):
    _add_column(db_session, filename="Spring.pptx", courses=("spring-video",))
    _add_column(db_session, filename="RAG.pptx", courses=("rag-video",))
    spring_sid = client.get(
        "/api/chat/column-session?course_id=spring-video", headers=_user_h()
    ).json()["session_id"]
    rag_sid = client.get(
        "/api/chat/column-session?course_id=rag-video", headers=_user_h()
    ).json()["session_id"]
    assert spring_sid != rag_sid

    admin_headers = {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}
    admin_sid = client.get(
        "/api/chat/column-session?course_id=spring-video", headers=admin_headers
    ).json()["session_id"]
    assert admin_sid != spring_sid
    rejected = client.post(
        "/api/chat/stream",
        json={"course_id": "spring-video", "session_id": admin_sid, "user_question": "越权"},
        headers=_user_h(),
    )
    assert rejected.status_code == 403


def test_column_session_imports_available_legacy_history_once(client, db_session):
    _add_column(db_session, courses=("legacy-video",))
    legacy = ChatSession(
        session_id="legacy-course-session",
        user_id=2,
        course_id="legacy-video",
        messages_json=json.dumps(
            [
                {"role": "user", "content": "旧问题"},
                {
                    "role": "assistant",
                    "content": "旧回答",
                    "model_name": "qwen-plus",
                    "context_meta": {"start_time": 33},
                },
            ],
            ensure_ascii=False,
        ),
    )
    db_session.add(legacy)
    db_session.commit()

    first = client.get(
        "/api/chat/column-session?course_id=legacy-video", headers=_user_h()
    ).json()
    second = client.get(
        "/api/chat/column-session?course_id=legacy-video", headers=_user_h()
    ).json()
    assert [item["content"] for item in first["messages"]] == ["旧问题", "旧回答"]
    assert second["messages"] == first["messages"]
    assert first["messages"][0]["start_time"] == 33
    canonical = db_session.query(ChatSession).filter(
        ChatSession.session_id == first["session_id"]
    ).one()
    assert [item["content"] for item in json.loads(canonical.messages_json)] == [
        "旧问题", "旧回答"
    ]


def test_failed_column_answer_does_not_enter_complete_history(client, db_session, monkeypatch):
    from app.api import chat

    _add_column(db_session, courses=("failed-video",))

    async def failed_stream(*_args):
        if False:
            yield ""
        raise LLMError(classify_provider_error(401, {"code": "InvalidApiKey"}))

    monkeypatch.setattr(chat, "stream_chat", failed_stream)
    response = client.post(
        "/api/chat/stream",
        json={"course_id": "failed-video", "user_question": "不会保存"},
        headers=_user_h(),
    )
    assert any(event.get("error") for event in _parse_sse(response))
    column_session = db_session.query(ColumnChatSession).one()
    assert db_session.query(ChatMessage).filter_by(
        session_id=column_session.session_id
    ).count() == 0
    assert db_session.query(ChatSession).filter_by(
        session_id=column_session.session_id
    ).one() is not None


def test_column_history_stays_complete_and_memory_summarizes_old_rounds(
    client, db_session, monkeypatch
):
    from app.api import chat

    _add_column(db_session, courses=("long-video",))
    captured_answer_messages = []

    async def routed(_db, config, _key, messages, outcome, _stream_fn):
        is_summary = "增量维护专栏对话" in messages[0]["content"]
        outcome.success = True
        outcome.model_name = config.model_name
        outcome.answer = "长期记忆：用户持续学习 Spring。" if is_summary else "回答"
        if not is_summary:
            captured_answer_messages.append(messages)
            yield {"delta": "回答"}
            yield {"done": True, "model_name": config.model_name}

    monkeypatch.setattr(chat, "stream_model_chain", routed)
    for index in range(16):
        response = client.post(
            "/api/chat/stream",
            json={"course_id": "long-video", "user_question": f"问题{index}"},
            headers=_user_h(),
        )
        assert response.status_code == 200

    column_session = db_session.query(ColumnChatSession).one()
    assert column_session.memory_summary.startswith("长期记忆")
    assert db_session.query(ChatMessage).filter_by(session_id=column_session.session_id).count() == 32
    mirror = json.loads(
        db_session.query(ChatSession).filter_by(session_id=column_session.session_id).one().messages_json
    )
    assert len(mirror) == 20
    final_prompt = captured_answer_messages[-1]
    assert "【专栏长期对话记忆】" in final_prompt[-1]["content"]
    assert len(final_prompt[1:-1]) == 20


@pytest.mark.asyncio
async def test_memory_summary_failure_keeps_pending_history(client, db_session, monkeypatch):
    from app.api import chat

    _add_column(db_session, courses=("summary-failure-video",))
    client.get(
        "/api/chat/column-session?course_id=summary-failure-video", headers=_user_h()
    )
    column_session = db_session.query(ColumnChatSession).one()
    for index in range(15):
        turn_id = f"manual-{index}"
        db_session.add_all(
            [
                ChatMessage(
                    session_id=column_session.session_id,
                    turn_id=turn_id,
                    role="user",
                    content=f"问题{index}",
                ),
                ChatMessage(
                    session_id=column_session.session_id,
                    turn_id=turn_id,
                    role="assistant",
                    content=f"回答{index}",
                ),
            ]
        )
    db_session.commit()

    async def broken_summary(*_args):
        if False:
            yield {}
        raise RuntimeError("summary failed")

    monkeypatch.setattr(chat, "stream_model_chain", broken_summary)
    pending = await chat._maybe_update_memory(
        db_session,
        column_session,
        db_session.query(ChatMessage).order_by(ChatMessage.id.asc()).all(),
        get_default_config(db_session),
        "sk-test",
    )
    assert len(pending) == 10
    pending_text = chat._pending_memory_text(pending)
    assert "问题0" in pending_text
    assert "回答4" in pending_text
    assert column_session.memory_summary == ""
    assert db_session.query(ChatMessage).count() == 30
