"""P4 字幕编辑器写回端点测试：GET/PUT /subtitle/cues。

覆盖：
- GET 返回 cues + revision 乐观锁指纹
- PUT 成功：写回 VTT、编辑使旧审核失效（review_state→unreviewed）、返回新 revision
- PUT 乐观锁冲突（revision 不匹配）→ 409
- PUT 非法时间轴 → 400
- PUT 字幕文件缺失 → 400
- PUT 课程不存在 → 404
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_materials import router as admin_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import Material, User
from app.services.subtitle import cue_revision, cues_to_vtt


@pytest.fixture()
def client(db_session):
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    db_session.add(admin)
    db_session.commit()

    def _get_db_override():
        yield db_session

    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _h():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def _make_ready_with_vtt(db_session, tmp_path, course_id="c1", cues=None):
    cues = cues or [{"start": 1.0, "end": 5.0, "text": "你好"}, {"start": 6.0, "end": 10.0, "text": "世界"}]
    vtt_path = tmp_path / f"{course_id}.whisper.vtt"
    vtt_path.write_text(cues_to_vtt(cues), encoding="utf-8")
    m = Material(
        course_id=course_id,
        dir_path=str(tmp_path),
        subtitle_path=str(vtt_path),
        subtitle_status="ready",
        subtitle_source="whisper",
        review_state="reviewed",
    )
    db_session.add(m)
    db_session.commit()
    return m, vtt_path


def test_get_subtitle_cues(client, db_session, tmp_path):
    _make_ready_with_vtt(db_session, tmp_path)
    resp = client.get("/api/admin/materials/c1/subtitle/cues", headers=_h())
    assert resp.status_code == 200
    body = resp.json()
    text = (tmp_path / "c1.whisper.vtt").read_text(encoding="utf-8")
    assert body["revision"] == cue_revision(text)
    assert len(body["cues"]) == 2
    assert body["cues"][0]["text"] == "你好"


def test_save_subtitle_cues_success_resets_review(client, db_session, tmp_path):
    _make_ready_with_vtt(db_session, tmp_path, cues=[{"start": 1.0, "end": 5.0, "text": "你好"}])
    rev = cue_revision((tmp_path / "c1.whisper.vtt").read_text(encoding="utf-8"))
    new_cues = [{"start": 1.0, "end": 4.0, "text": "你好改"}, {"start": 5.0, "end": 9.0, "text": "世界"}]
    resp = client.put(
        "/api/admin/materials/c1/subtitle/cues",
        json={"cues": new_cues, "revision": rev},
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["review_state"] == "unreviewed"  # 编辑使旧审核失效
    content = (tmp_path / "c1.whisper.vtt").read_text(encoding="utf-8")
    assert "你好改" in content
    assert body["revision"] == cue_revision(content)
    db_session.expire_all()
    assert db_session.query(Material).filter(Material.course_id == "c1").one().review_state == "unreviewed"


def test_save_subtitle_cues_revision_conflict_409(client, db_session, tmp_path):
    _make_ready_with_vtt(db_session, tmp_path)
    resp = client.put(
        "/api/admin/materials/c1/subtitle/cues",
        json={"cues": [{"start": 1.0, "end": 5.0, "text": "x"}], "revision": "deadbeef"},
        headers=_h(),
    )
    assert resp.status_code == 409


def test_save_subtitle_cues_invalid_time_axis_400(client, db_session, tmp_path):
    _make_ready_with_vtt(db_session, tmp_path)
    rev = cue_revision((tmp_path / "c1.whisper.vtt").read_text(encoding="utf-8"))
    # 结束 <= 开始
    r1 = client.put(
        "/api/admin/materials/c1/subtitle/cues",
        json={"cues": [{"start": 5.0, "end": 2.0, "text": "bad"}], "revision": rev},
        headers=_h(),
    )
    assert r1.status_code == 400
    # 负开始时间
    r2 = client.put(
        "/api/admin/materials/c1/subtitle/cues",
        json={"cues": [{"start": -1.0, "end": 2.0, "text": "bad"}], "revision": rev},
        headers=_h(),
    )
    assert r2.status_code == 400


def test_save_subtitle_cues_missing_file_400(client, db_session, tmp_path):
    vtt_path = tmp_path / "c1.whisper.vtt"  # 故意不写文件
    m = Material(
        course_id="c1", dir_path=str(tmp_path), subtitle_path=str(vtt_path),
        subtitle_status="ready", subtitle_source="whisper", review_state="reviewed",
    )
    db_session.add(m)
    db_session.commit()
    resp = client.put(
        "/api/admin/materials/c1/subtitle/cues",
        json={"cues": [{"start": 1.0, "end": 5.0, "text": "x"}], "revision": "anything"},
        headers=_h(),
    )
    assert resp.status_code == 400


def test_save_subtitle_cues_404_when_no_course(client, db_session, tmp_path):
    resp = client.put(
        "/api/admin/materials/nope/subtitle/cues",
        json={"cues": [], "revision": "x"},
        headers=_h(),
    )
    assert resp.status_code == 404
