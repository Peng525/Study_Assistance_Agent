"""P4 字幕编辑器写回端点测试：GET/PUT /subtitle/cues。

覆盖：
- GET 返回 cues + revision 乐观锁指纹
- PUT 成功：写回 VTT、标记人工校对完成（review_state→reviewed）、返回新 revision
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
    assert body["review_state"] == "reviewed"
    content = (tmp_path / "c1.whisper.vtt").read_text(encoding="utf-8")
    assert "你好改" in content
    assert body["revision"] == cue_revision(content)
    db_session.expire_all()
    assert db_session.query(Material).filter(Material.course_id == "c1").one().review_state == "reviewed"


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


# ---------- v9 批次 2 · B2-2：cues 写回的并发约束 ----------


def test_revision_is_computed_while_holding_write_lock(client, db_session, tmp_path, monkeypatch):
    """check-then-act 必须原子化：校验用的 revision 要在**持锁期间**计算。

    在锁外算 revision 的话，两个并发请求会读到同一个指纹并双双通过校验，
    后写的覆盖先写的，而双方都收到「保存成功」—— 乐观锁形同虚设（PRD §5.5A.6）。
    """
    from app.api import admin_materials as am

    _make_ready_with_vtt(db_session, tmp_path)
    vtt = tmp_path / "c1.whisper.vtt"
    rev = cue_revision(vtt.read_text(encoding="utf-8"))

    seen = []
    orig = am.cue_revision

    def spy(text):
        seen.append(am._CUES_WRITE_LOCK.locked())
        return orig(text)

    monkeypatch.setattr(am, "cue_revision", spy)

    resp = client.put(
        "/api/admin/materials/c1/subtitle/cues",
        headers=_h(),
        json={"revision": rev, "cues": [{"start": 1.0, "end": 5.0, "text": "改"}]},
    )
    assert resp.status_code == 200
    assert seen, "PUT 期间应该算过 revision"
    # 第 1 次是校验用的（锁内），后面还有一次是算新 revision 返回给前端（锁外，无所谓）
    assert seen[0] is True, "校验用的 revision 必须在持锁期间计算，否则 check-then-act 不原子"


def test_cues_tmp_name_is_unique(client, db_session, tmp_path, monkeypatch):
    """临时文件名唯一化：固定名会让并发请求写同一个 tmp。

    `replace` 的原子性只保证单次 swap，不保证 swap 进去的内容是谁写的 ——
    两个请求写同一个 `xxx.vtt.tmp` 时，后 replace 的可能把先写的内容换进去。
    """
    import pathlib

    _make_ready_with_vtt(db_session, tmp_path)
    vtt = tmp_path / "c1.whisper.vtt"

    names = []
    orig_replace = pathlib.Path.replace

    def spy_replace(self, target):
        names.append(self.name)
        return orig_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", spy_replace)

    for text in ("第一次", "第二次"):
        rev = cue_revision(vtt.read_text(encoding="utf-8"))
        resp = client.put(
            "/api/admin/materials/c1/subtitle/cues",
            headers=_h(),
            json={"revision": rev, "cues": [{"start": 1.0, "end": 5.0, "text": text}]},
        )
        assert resp.status_code == 200, resp.text

    assert len(names) == 2, f"应该写回两次，实际 {names}"
    assert names[0] != names[1], "两次写回不能共用同一个 tmp 名"
    assert all(n.startswith("c1.whisper.vtt.") and n.endswith(".tmp") for n in names), names


def test_cues_write_failure_leaves_no_tmp_residue(client, db_session, tmp_path, monkeypatch):
    """写回失败必须删掉 tmp —— 残留的 .tmp 会被素材扫描当成字幕文件。"""
    import pathlib

    _make_ready_with_vtt(db_session, tmp_path)
    vtt = tmp_path / "c1.whisper.vtt"
    rev = cue_revision(vtt.read_text(encoding="utf-8"))

    orig_replace = pathlib.Path.replace

    def boom_on_tmp(self, target):
        if self.name.endswith(".tmp"):
            raise OSError("replace failed (simulated)")
        return orig_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", boom_on_tmp)

    with pytest.raises(OSError):
        client.put(
            "/api/admin/materials/c1/subtitle/cues",
            headers=_h(),
            json={"revision": rev, "cues": [{"start": 1.0, "end": 5.0, "text": "改"}]},
        )

    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == [], f"写回失败后不应残留 tmp：{leftovers}"
    assert vtt.read_text(encoding="utf-8") == cues_to_vtt(
        [{"start": 1.0, "end": 5.0, "text": "你好"}, {"start": 6.0, "end": 10.0, "text": "世界"}]
    ), "写回失败不应破坏原字幕文件"
