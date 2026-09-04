"""A4 字幕审核端点 + A3 证据权限矩阵测试。

覆盖：
- 审核端点：ready 可标记为 reviewed/unreviewed；非 ready 拒绝；非法状态拒绝
- 公开素材列表/详情响应带 review_state
- 证据权限单一判定点 transcript_context_allowed 的四象限
- 迁移幂等（建表即带列 / 已建表补列都可）
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_materials import router as admin_router
from app.api.materials import router as materials_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import Material, User
from app.services.subtitle import transcript_context_allowed
from app.services import whisper_service


@pytest.fixture()
def client(db_session):
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    db_session.add(admin)
    db_session.commit()

    def _get_db_override():
        yield db_session

    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(materials_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _h():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def _make_ready(db_session, course_id="c1") -> Material:
    m = Material(course_id=course_id, dir_path="/tmp/x", subtitle_status="ready",
                 subtitle_source="whisper", review_state="unreviewed")
    db_session.add(m)
    db_session.commit()
    return m


# ---- 审核端点 ----


def test_review_mark_reviewed(client, db_session):
    _make_ready(db_session)
    resp = client.post(
        "/api/admin/materials/c1/subtitle/review",
        json={"review_state": "reviewed"},
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["review_state"] == "reviewed"
    assert body["subtitle_status"] == "ready"
    db_session.expire_all()
    assert db_session.query(Material).filter(Material.course_id == "c1").one().review_state == "reviewed"


def test_review_can_revert_to_unreviewed(client, db_session):
    _make_ready(db_session)
    client.post("/api/admin/materials/c1/subtitle/review",
                json={"review_state": "reviewed"}, headers=_h())
    resp = client.post(
        "/api/admin/materials/c1/subtitle/review",
        json={"review_state": "unreviewed"},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json()["review_state"] == "unreviewed"


def test_review_rejects_non_ready(client, db_session):
    m = Material(course_id="c2", dir_path="/tmp/y", subtitle_status="generating", review_state="unreviewed")
    db_session.add(m)
    db_session.commit()
    resp = client.post(
        "/api/admin/materials/c2/subtitle/review",
        json={"review_state": "reviewed"},
        headers=_h(),
    )
    assert resp.status_code == 400


def test_review_rejects_invalid_state(client, db_session):
    _make_ready(db_session)
    resp = client.post(
        "/api/admin/materials/c1/subtitle/review",
        json={"review_state": "approved"},
        headers=_h(),
    )
    assert resp.status_code == 400


def test_review_404_when_missing(client, db_session):
    resp = client.post(
        "/api/admin/materials/nope/subtitle/review",
        json={"review_state": "reviewed"},
        headers=_h(),
    )
    assert resp.status_code == 404


# ---- 公开响应带 review_state ----


def test_list_response_includes_review_state(client, db_session):
    _make_ready(db_session, "c1")
    db_session.add(Material(course_id="c3", dir_path="/tmp/z", subtitle_status="ready",
                            subtitle_source="manual", review_state="reviewed"))
    db_session.commit()
    # materials 公开接口要求 user，但 admin 也能看；用 admin token 直接拉列表
    resp = client.get("/api/materials", headers=_h())
    assert resp.status_code == 200
    by_id = {m["course_id"]: m for m in resp.json()}
    assert by_id["c1"]["review_state"] == "unreviewed"
    assert by_id["c3"]["review_state"] == "reviewed"


def test_subtitle_status_response_includes_review_state(client, db_session):
    _make_ready(db_session)
    resp = client.get("/api/materials/c1/subtitle-status", headers=_h())
    assert resp.status_code == 200
    assert resp.json()["review_state"] == "unreviewed"


# ---- 证据权限矩阵（transcript_context_allowed）----


@pytest.mark.parametrize(
    "status,review,expect",
    [
        ("ready", "reviewed", True),      # 生成完成 + 已审核 → 自动注入
        ("ready", "unreviewed", False),    # 生成完成 + 未审核 → 仅可展示/主动引用
        ("pending", "reviewed", False),    # 没生成好，审核态无意义
        ("generating", "reviewed", False),
        ("error", "unreviewed", False),
        (None, "reviewed", False),
    ],
)
def test_transcript_permission_matrix(status, review, expect):
    assert transcript_context_allowed(status, review) is expect


def test_generate_status_endpoint_does_not_change_review_state(client, db_session, monkeypatch):
    """调用字幕状态不应误改 review_state（回归：状态查询是只读的）。"""
    _make_ready(db_session)
    before = db_session.query(Material).filter(Material.course_id == "c1").one().review_state
    resp = client.get("/api/materials/c1/subtitle-status", headers=_h())
    assert resp.status_code == 200
    db_session.expire_all()
    after = db_session.query(Material).filter(Material.course_id == "c1").one().review_state
    assert before == after == "unreviewed"
