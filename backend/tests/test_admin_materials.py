"""模块 2.1/2.2/2.5 素材上传/列表/删除/扫描测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_materials import router as materials_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import Material, ProjectSource, User, VideoKnowledge
from app.services import storage
from app.services.project_context import ensure_default_project


@pytest.fixture()
def client(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_materials_root", lambda: tmp_path)

    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    user = User(username="user25", password_hash=hash_password("123456"), role="user")
    db_session.add_all([admin, user])
    db_session.commit()

    def _get_db_override():
        yield db_session

    app = FastAPI()
    app.include_router(materials_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _h():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def test_upload_video_success(client, db_session):
    resp = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "c1", "file_type": "video"},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftypmp42 rest", "video/mp4")},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json()["course_id"] == "c1"
    material = db_session.query(Material).filter(Material.course_id == "c1").one()
    context = db_session.query(VideoKnowledge).filter(VideoKnowledge.material_id == material.id).one()
    assert context.course_type == "theory"


def test_upload_video_can_select_practice_and_reject_path_course_id(client, db_session):
    practice = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "case-1", "file_type": "video", "course_type": "practice"},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftypmp42 rest", "video/mp4")},
        headers=_h(),
    )
    assert practice.status_code == 200
    material = db_session.query(Material).filter(Material.course_id == "case-1").one()
    assert db_session.query(VideoKnowledge).filter(
        VideoKnowledge.material_id == material.id
    ).one().course_type == "practice"

    unsafe = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "../escape", "file_type": "video"},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftypmp42 rest", "video/mp4")},
        headers=_h(),
    )
    assert unsafe.status_code == 400
    assert "路径" in unsafe.json()["detail"]


def test_upload_video_can_bind_ppt_column(client, db_session, tmp_path):
    project = ensure_default_project(db_session)
    source = ProjectSource(
        project_id=project.id,
        original_filename="Spring.pptx",
        source_format="pptx",
        file_path=str(tmp_path / "Spring.pptx"),
        text_cached="【第1页】\nSpring",
        source_hash="a" * 64,
        status="active",
    )
    db_session.add(source)
    db_session.commit()
    response = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "spring-1", "file_type": "video", "source_id": source.id},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftypmp42 rest", "video/mp4")},
        headers=_h(),
    )
    assert response.status_code == 200
    material = db_session.query(Material).filter_by(course_id="spring-1").one()
    knowledge = db_session.query(VideoKnowledge).filter_by(material_id=material.id).one()
    assert knowledge.source_id == source.id

    invalid = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "bad-source", "file_type": "video", "source_id": 9999},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftypmp42 rest", "video/mp4")},
        headers=_h(),
    )
    assert invalid.status_code == 400
    assert db_session.query(Material).filter_by(course_id="bad-source").first() is None


def test_upload_wrong_extension(client):
    resp = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "c1", "file_type": "video"},
        files={"file": ("v.avi", b"whatever", "video/x-msvideo")},
        headers=_h(),
    )
    assert resp.status_code == 400
    assert "仅支持" in resp.json()["detail"]


def test_upload_magic_mismatch(client):
    resp = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "c1", "file_type": "video"},
        files={"file": ("v.mp4", b"not a real mp4 at all", "video/mp4")},
        headers=_h(),
    )
    assert resp.status_code == 400
    assert "不符" in resp.json()["detail"]


def test_upload_reject_unsupported_subtitle(client):
    resp = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "c1", "file_type": "subtitle"},
        files={"file": ("s.srt", b"[Script Info]\nTitle: x\n[Events]\n", "text/plain")},
        headers=_h(),
    )
    assert resp.status_code == 400
    assert "ASS/SSA" in resp.json()["detail"]


def test_upload_requires_admin(client):
    token = create_access_token(2, "user25", "user")
    resp = client.post(
        "/api/admin/materials/upload",
        params={"course_id": "c1", "file_type": "video"},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftyp", "video/mp4")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_list_files(client):
    client.post(
        "/api/admin/materials/upload",
        params={"course_id": "c1", "file_type": "video"},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
        headers=_h(),
    )
    resp = client.get("/api/admin/materials/c1/files", headers=_h())
    assert resp.status_code == 200
    assert len(resp.json()["files"]) == 1


def test_delete_file(client):
    client.post(
        "/api/admin/materials/upload",
        params={"course_id": "c1", "file_type": "video"},
        files={"file": ("v.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
        headers=_h(),
    )
    resp = client.delete("/api/admin/materials/c1/files/video", headers=_h())
    assert resp.status_code == 200
    resp2 = client.get("/api/admin/materials/c1/files", headers=_h())
    assert resp2.json()["files"] == []


def test_scan_no_video_marks_error(client, tmp_path, db_session):
    # 只放课件，不放视频 → 扫描后 status=error
    (tmp_path / "c2").mkdir()
    (tmp_path / "c2" / "course.md").write_text("# 标题\n内容", encoding="utf-8")
    resp = client.post("/api/admin/materials/scan", headers=_h())
    assert resp.status_code == 200
    m = db_session.query(Material).filter(Material.course_id == "c2").first()
    assert m is not None
    assert m.status == "error"
    assert "缺少视频" in m.error_message
