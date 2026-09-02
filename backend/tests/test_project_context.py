"""项目共享资料、Summary 审核发布和版本快照测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_project_context import router
from app.core.database import get_db
from app.core.security import create_access_token, encrypt_api_key, hash_password
from app.models.models import (
    ChatContextBinding,
    Material,
    ModelConfig,
    ProjectChunk,
    ProjectContextVersion,
    ProjectSource,
    User,
    VideoKnowledge,
)
from app.services.project_context import (
    PROJECT_EVIDENCE_TOKEN_BUDGET,
    build_evidence_text,
    ensure_default_project,
    bind_material,
    get_or_create_session_binding,
    manifest_json,
    select_evidence_chunks,
    snapshot_chunks,
    version_context,
)


@pytest.fixture()
def client(db_session, tmp_path, monkeypatch):
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    db_session.add(admin)
    db_session.add(
        ModelConfig(
            name="default",
            base_url="https://example.test/v1",
            api_key_encrypted=encrypt_api_key("sk-test"),
            model_name="qwen-plus",
            is_default=True,
        )
    )
    db_session.commit()

    from app.api import admin_project_context

    monkeypatch.setattr(admin_project_context, "source_storage_root", lambda: tmp_path)
    monkeypatch.setattr(admin_project_context.storage, "_materials_root", lambda: tmp_path / "materials")

    async def fake_chain(db, config, api_key, messages, outcome, stream_fn, *, deadline_seconds):
        assert deadline_seconds is None
        outcome.success = True
        if "课程大纲整理助手" in messages[0]["content"]:
            assert "不设置固定字数上限" in messages[0]["content"]
            outcome.answer = "# 视频大纲\n仅依据所选 PPT 页生成"
        else:
            assert "只能依据给定资料" in messages[0]["content"]
            assert "不设置固定字数上限" in messages[0]["content"]
            assert "内部代号 Aurora-17" in messages[1]["content"]
            outcome.answer = "# 项目定位\n内部教学项目 Aurora-17\n\n# 来源清单\nproject.md"
        yield {"done": True, "model_name": "qwen-plus"}

    monkeypatch.setattr(admin_project_context, "stream_model_chain", fake_chain)

    def override_db():
        yield db_session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _headers():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def test_summary_requires_human_publish_and_source_change_marks_stale(client, db_session):
    upload = client.post(
        "/api/admin/project-context/sources",
        files={"file": ("project.md", "# 项目\n内部代号 Aurora-17".encode(), "text/markdown")},
        headers=_headers(),
    )
    if upload.status_code != 200:
        pytest.fail(upload.text)
    assert upload.json()["summary_refresh_required"] is True

    generated = client.post("/api/admin/project-context/summary/generate", headers=_headers())
    assert generated.status_code == 200
    draft = generated.json()["draft"]
    assert draft["status"] == "draft"
    assert client.get("/api/admin/project-context", headers=_headers()).json()["published"] is None

    published = client.post(
        "/api/admin/project-context/summary/publish",
        json={"version_id": draft["id"]},
        headers=_headers(),
    )
    assert published.status_code == 200
    assert published.json()["published"]["version"] == 1
    assert db_session.query(ProjectChunk).count() >= 1

    second = client.post(
        "/api/admin/project-context/sources",
        files={"file": ("more.md", "# 约束\n只能本地运行".encode(), "text/markdown")},
        headers=_headers(),
    )
    assert second.status_code == 200
    state = client.get("/api/admin/project-context", headers=_headers()).json()
    assert state["published"]["is_stale"] is True
    assert state["published"]["summary_text"] == published.json()["published"]["summary_text"]


def test_publish_rejects_draft_when_sources_changed(client):
    client.post(
        "/api/admin/project-context/sources",
        files={"file": ("project.md", "# 项目\n内部代号 Aurora-17".encode(), "text/markdown")},
        headers=_headers(),
    )
    draft = client.post("/api/admin/project-context/summary/generate", headers=_headers()).json()["draft"]
    client.post(
        "/api/admin/project-context/sources",
        files={"file": ("changed.md", "# 新资料\n事实变化".encode(), "text/markdown")},
        headers=_headers(),
    )
    response = client.post(
        "/api/admin/project-context/summary/publish",
        json={"version_id": draft["id"]},
        headers=_headers(),
    )
    assert response.status_code == 409


def test_large_sources_can_use_manual_draft_when_ai_generation_is_refused(client, db_session):
    uploaded = client.post(
        "/api/admin/project-context/sources",
        files={"file": ("large.md", ("# 背景\n" + "项目事实" * 5_100).encode(), "text/markdown")},
        headers=_headers(),
    )
    assert uploaded.status_code == 200
    refused = client.post("/api/admin/project-context/summary/generate", headers=_headers())
    assert refused.status_code == 400
    assert "超过摘要生成上限" in refused.json()["detail"]

    manual = client.put(
        "/api/admin/project-context/summary/draft",
        json={"version_id": None, "summary_text": "# 项目定位\n人工审核的大资料摘要"},
        headers=_headers(),
    )
    assert manual.status_code == 200
    published = client.post(
        "/api/admin/project-context/summary/publish",
        json={"version_id": manual.json()["draft"]["id"]},
        headers=_headers(),
    )
    assert published.status_code == 200
    assert db_session.query(ProjectChunk).count() > 8


def test_summary_draft_has_no_independent_2k_limit(client):
    client.post(
        "/api/admin/project-context/sources",
        files={"file": ("project.md", "# 项目\n内部代号 Aurora-17".encode(), "text/markdown")},
        headers=_headers(),
    )
    long_summary = "# 项目定位\n" + "完整项目事实" * 600
    saved = client.put(
        "/api/admin/project-context/summary/draft",
        json={"version_id": None, "summary_text": long_summary},
        headers=_headers(),
    )
    assert saved.status_code == 200
    assert saved.json()["draft"]["summary_text"] == long_summary


def test_shared_ppt_maps_different_pages_to_two_videos(client, db_session, tmp_path):
    project = ensure_default_project(db_session)
    materials = [
        Material(course_id="video-a", dir_path=str(tmp_path / "materials/video-a"), status="ready"),
        Material(course_id="video-b", dir_path=str(tmp_path / "materials/video-b"), status="ready"),
    ]
    db_session.add_all(materials)
    db_session.flush()
    for material in materials:
        bind_material(db_session, material, project)
    source = ProjectSource(
        project_id=project.id,
        original_filename="Spring.pptx",
        source_format="pptx",
        file_path=str(tmp_path / "Spring.pptx"),
        text_cached=(
            "【第1页 Spring】\n总览\n"
            "【第2页 IoC】\n控制反转\n"
            "【第3页 DI】\n依赖注入\n"
            "【第4页 AOP】\n切面编程"
        ),
        source_hash="a" * 64,
        status="active",
    )
    db_session.add(source)
    db_session.commit()

    # GET 只能读取旧视频状态，不能偷偷创建 theory 配置并切断旧上下文。
    state = client.get("/api/admin/project-context", headers=_headers())
    assert state.status_code == 200
    assert all(video["legacy_context"] for video in state.json()["videos"])
    assert db_session.query(VideoKnowledge).count() == 0

    mapped_a = client.put(
        "/api/admin/project-context/videos/video-a/knowledge",
        json={"source_id": source.id, "page_start": 1, "page_end": 2, "course_type": "theory"},
        headers=_headers(),
    )
    mapped_b = client.put(
        "/api/admin/project-context/videos/video-b/knowledge",
        json={"source_id": source.id, "page_start": 3, "page_end": 4, "course_type": "practice"},
        headers=_headers(),
    )
    assert mapped_a.status_code == mapped_b.status_code == 200
    text_a = mapped_a.json()["video"]["knowledge_text"]
    text_b = mapped_b.json()["video"]["knowledge_text"]
    assert "控制反转" in text_a and "依赖注入" not in text_a
    assert "依赖注入" in text_b and "控制反转" not in text_b
    assert mapped_a.json()["video"]["source_id"] == mapped_b.json()["video"]["source_id"]
    assert (tmp_path / "materials/video-a/_knowledge/course-knowledge.md").exists()
    assert (tmp_path / "materials/video-b/_knowledge/course-knowledge.md").exists()

    in_use = client.delete(
        f"/api/admin/project-context/sources/{source.id}", headers=_headers()
    )
    assert in_use.status_code == 409


def test_practice_outline_is_draft_until_admin_saves_it(client, db_session, tmp_path):
    project = ensure_default_project(db_session)
    material = Material(course_id="case-video", dir_path=str(tmp_path / "case-video"), status="ready")
    db_session.add(material)
    db_session.flush()
    bind_material(db_session, material, project)
    source = ProjectSource(
        project_id=project.id,
        original_filename="case.pptx",
        source_format="pptx",
        file_path=str(tmp_path / "case.pptx"),
        text_cached="【第1页 案例】\n内部步骤 Alpha",
        source_hash="b" * 64,
        status="active",
    )
    db_session.add(source)
    db_session.commit()
    mapped = client.put(
        "/api/admin/project-context/videos/case-video/knowledge",
        json={"source_id": source.id, "page_start": 1, "page_end": 1, "course_type": "practice"},
        headers=_headers(),
    )
    assert mapped.status_code == 200
    generated = client.post(
        "/api/admin/project-context/videos/case-video/outline/generate", headers=_headers()
    )
    assert generated.status_code == 200
    assert generated.json()["video"]["outline_status"] == "draft"
    saved = client.put(
        "/api/admin/project-context/videos/case-video/outline",
        json={"outline_text": generated.json()["video"]["outline_text"]},
        headers=_headers(),
    )
    assert saved.status_code == 200
    assert saved.json()["video"]["outline_status"] == "ready"


def test_deleting_source_marks_published_summary_stale(client):
    uploaded = client.post(
        "/api/admin/project-context/sources",
        files={"file": ("project.md", "# 项目\n内部代号 Aurora-17".encode(), "text/markdown")},
        headers=_headers(),
    )
    source_id = uploaded.json()["source"]["id"]
    draft = client.post("/api/admin/project-context/summary/generate", headers=_headers()).json()["draft"]
    assert client.post(
        "/api/admin/project-context/summary/publish",
        json={"version_id": draft["id"]},
        headers=_headers(),
    ).status_code == 200

    deleted = client.delete(f"/api/admin/project-context/sources/{source_id}", headers=_headers())
    assert deleted.status_code == 200
    assert deleted.json()["summary_refresh_required"] is True
    state = client.get("/api/admin/project-context", headers=_headers()).json()
    assert state["sources"] == []
    assert state["published"]["is_stale"] is True


def test_existing_session_remains_bound_to_superseded_summary(db_session):
    project = ensure_default_project(db_session)
    source_v1 = ProjectSource(
        project_id=project.id,
        original_filename="v1.md",
        source_format="md",
        file_path="v1.md",
        text_cached="# 版本一\n旧会话专用事实 Alpha。",
        source_hash="1" * 64,
        status="active",
    )
    db_session.add(source_v1)
    db_session.flush()
    version_v1 = ProjectContextVersion(
        project_id=project.id,
        version=1,
        summary_text="已审核摘要 v1",
        source_manifest_json=manifest_json([source_v1]),
        status="published",
    )
    db_session.add(version_v1)
    db_session.flush()
    snapshot_chunks(db_session, version_v1, [source_v1])
    binding = get_or_create_session_binding(db_session, "existing-session", project)
    assert binding is not None

    version_v1.status = "superseded"
    source_v2 = ProjectSource(
        project_id=project.id,
        original_filename="v2.md",
        source_format="md",
        file_path="v2.md",
        text_cached="# 版本二\n新会话专用事实 Beta。",
        source_hash="2" * 64,
        status="active",
    )
    db_session.add(source_v2)
    db_session.flush()
    version_v2 = ProjectContextVersion(
        project_id=project.id,
        version=2,
        summary_text="已审核摘要 v2",
        source_manifest_json=manifest_json([source_v2]),
        status="published",
    )
    db_session.add(version_v2)
    db_session.flush()
    snapshot_chunks(db_session, version_v2, [source_v2])
    db_session.commit()

    same_binding = get_or_create_session_binding(db_session, "existing-session", project)
    new_binding = get_or_create_session_binding(db_session, "new-session", project)
    db_session.commit()
    assert same_binding is not None and same_binding.context_version_id == version_v1.id
    assert new_binding is not None and new_binding.context_version_id == version_v2.id
    old_summary, old_evidence, old_meta = version_context(db_session, same_binding, "事实是什么？")
    new_summary, new_evidence, new_meta = version_context(db_session, new_binding, "事实是什么？")
    assert old_summary == "已审核摘要 v1"
    assert "Alpha" in old_evidence and "Beta" not in old_evidence
    assert old_meta["project_context_version"] == 1
    assert new_summary == "已审核摘要 v2"
    assert "Beta" in new_evidence and "Alpha" not in new_evidence
    assert new_meta["project_context_version"] == 2


def test_large_source_uses_chinese_fts_and_superseded_version_remains_available(db_session):
    project = ensure_default_project(db_session)
    source = ProjectSource(
        project_id=project.id,
        original_filename="architecture.md",
        source_format="md",
        file_path="architecture.md",
        text_cached=("普通背景说明" * 2_000) + "\n\n# 部署约束\n内部代号星河协议，只允许离线部署。",
        source_hash="b" * 64,
        status="active",
    )
    db_session.add(source)
    db_session.flush()
    version = ProjectContextVersion(
        project_id=project.id,
        version=1,
        summary_text="已审核摘要",
        source_manifest_json=manifest_json([source]),
        status="superseded",
    )
    db_session.add(version)
    db_session.flush()
    snapshot_chunks(db_session, version, [source])
    binding = ChatContextBinding(
        session_id="old-session",
        project_id=project.id,
        context_version_id=version.id,
    )
    db_session.add(binding)
    db_session.commit()

    long_question = "请根据现有全部项目资料详细分析星河协议如何部署"
    chunks, mode = select_evidence_chunks(db_session, version, long_question)
    assert mode == "fts5"
    assert any("只允许离线部署" in chunk.content for chunk in chunks)

    summary, evidence, metadata = version_context(db_session, binding, long_question)
    assert summary == "已审核摘要"
    assert "只允许离线部署" in evidence
    assert metadata["project_context_version"] == 1

    fallback_chunks, fallback_mode = select_evidence_chunks(db_session, version, "？")
    assert fallback_mode == "fallback"
    assert len(build_evidence_text(fallback_chunks)) <= PROJECT_EVIDENCE_TOKEN_BUDGET
