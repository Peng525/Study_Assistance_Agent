"""模块 2.1/2.2/2.5 素材上传/列表/删除/扫描测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_materials import router as materials_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import Material, ProjectSource, User, VideoKnowledge
from app.services import storage
from app.services import whisper_service
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


# ---------- v9 S1：单条取消端点的契约收紧（AC-20） ----------


def _reset_whisper_state():
    """清空 `whisper_service` 的模块级全局状态。

    `_tasks` / `_queue` / `_cancel_requested` 都是**模块级全局**，跨用例共享。
    本文件前面的用例（上传 → 自动扫描 → 无字幕自动 enqueue）会往里塞真实任务，
    不清理的话"没有真实任务"这个前提就是假的，取消测试会假绿。
    """
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._cancel_requested.clear()
    whisper_service._worker_running = False


def test_cancel_subtitle_returns_400_when_no_real_task(client, db_session, tmp_path):
    """DB 写着 generating 但 runtime 无任务 → 单条取消必须 400，不许假成功（AC-20）。

    批量端点返回 per-item `ok=false`；单条端点必须给 400。
    两种形态下的语义必须一致：**没有真实任务 = 没取消成**。
    """
    _reset_whisper_state()

    course_dir = tmp_path / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    (course_dir / "v.mp4").write_bytes(b"\x00" * 16)
    db_session.add(
        Material(
            course_id="c1",
            dir_path=str(course_dir),
            status="ready",
            subtitle_status="generating",   # 进程重启残留的假 generating（orphan）
        )
    )
    db_session.commit()

    assert whisper_service.task_exists("c1") is False, "前置：runtime 里确实没有任务"
    resp = client.post("/api/admin/materials/c1/cancel-subtitle", headers=_h())
    assert resp.status_code == 400, "没有真实任务却返回成功，是最难排查的一类事故"
    assert "队列中无该任务" in resp.json()["detail"]


def test_cancel_subtitle_returns_400_for_pending_status(client, db_session, tmp_path):
    """`pending` 不在 CANCELLABLE 白名单内 → 400。

    `pending` 表示从未排上任务，内存里没有 TaskState，谈不上取消。
    """
    _reset_whisper_state()

    course_dir = tmp_path / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    (course_dir / "v.mp4").write_bytes(b"\x00" * 16)
    db_session.add(
        Material(
            course_id="c1",
            dir_path=str(course_dir),
            status="ready",
            subtitle_status="pending",
        )
    )
    db_session.commit()

    resp = client.post("/api/admin/materials/c1/cancel-subtitle", headers=_h())
    assert resp.status_code == 400
    assert "不可取消" in resp.json()["detail"]
