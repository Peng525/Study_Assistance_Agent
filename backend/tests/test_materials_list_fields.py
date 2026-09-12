"""v8 §5.5A.7 素材列表端点字段契约测试。

覆盖：
- 新增字段对 admin 全部可见（含 `subtitle_error`）
- `subtitle_error` 对 user 角色恒为 None（可能含服务器绝对路径与异常原文）
- `subtitle_relative_path` 是项目相对路径；项目外路径只暴露文件名，不泄露目录结构
- 运行态字段（progress / slices / phase / started_at / queue_position）从内存透出
- **列表查询不得凭空造 TaskState**（否则 `active_task_count()` 被灌水）
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.materials import router as materials_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import ContentSeries, Material, User, VideoKnowledge
from app.services import storage, whisper_service
from app.services.project_context import ensure_default_project


@pytest.fixture()
def client(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_materials_root", lambda: tmp_path / "materials")

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


def _h(role: str, user_id: int):
    return {"Authorization": f"Bearer {create_access_token(user_id, role, role)}"}


def _add(db, course_id, *, subtitle_status="ready", review_state="unreviewed", **kw):
    material = Material(
        course_id=course_id,
        dir_path=kw.pop("dir_path", f"/m/{course_id}"),
        status="ready",
        subtitle_status=subtitle_status,
        review_state=review_state,
        **kw,
    )
    db.add(material)
    db.commit()
    return material


def _row(rows, course_id):
    return next(r for r in rows if r["course_id"] == course_id)


@pytest.fixture(autouse=True)
def _clean_whisper_state():
    yield
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()


def test_admin_sees_all_subtitle_fields(client, db_session, tmp_path):
    course_dir = tmp_path / "materials" / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    subtitle = course_dir / "v.whisper.vtt"
    subtitle.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n", encoding="utf-8")

    _add(
        db_session, "c1",
        subtitle_path=str(subtitle),
        subtitle_source="whisper",
        subtitle_error="转写炸了 /srv/secret/path",
    )

    row = _row(client.get("/api/materials", headers=_h("admin", 1)).json(), "c1")

    assert row["subtitle_source"] == "whisper"
    assert row["subtitle_filename"] == "v.whisper.vtt"
    # 相对路径：不含 tmp_path 这个绝对路径
    assert row["subtitle_relative_path"] == "materials/c1/v.whisper.vtt"
    assert str(tmp_path) not in (row["subtitle_relative_path"] or "")
    assert row["subtitle_error"] == "转写炸了 /srv/secret/path"


def test_user_cannot_see_subtitle_error(client, db_session, tmp_path):
    """subtitle_error 可能含服务器内部路径与异常原文，user 角色必须拿不到。"""
    course_dir = tmp_path / "materials" / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    subtitle = course_dir / "v.whisper.vtt"
    subtitle.write_text("WEBVTT\n", encoding="utf-8")

    material = _add(db_session, "c1", subtitle_path=str(subtitle), subtitle_error="/srv/secret leak")
    project = ensure_default_project(db_session)
    series = ContentSeries(project_id=project.id, name="Spring", normalized_name="spring")
    db_session.add(series)
    db_session.flush()
    db_session.add(VideoKnowledge(material_id=material.id, series_id=series.id))
    db_session.commit()

    row = _row(client.get("/api/materials", headers=_h("user", 2)).json(), "c1")

    assert row["subtitle_error"] is None, "user 不该看到 subtitle_error"
    # 非敏感字段仍可见
    assert row["subtitle_filename"] == "v.whisper.vtt"
    assert row["subtitle_status"] == "ready"


def test_relative_path_inside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_materials_root", lambda: tmp_path / "materials")
    from app.api.materials import _relative_subtitle_path

    inside = tmp_path / "materials" / "c1" / "v.whisper.vtt"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("WEBVTT\n", encoding="utf-8")

    assert _relative_subtitle_path(str(inside)) == "materials/c1/v.whisper.vtt"


def test_relative_path_outside_root_falls_back_to_filename(tmp_path, monkeypatch):
    """字幕落在项目外（历史数据 / 异常）→ 只给文件名，绝不泄露服务器目录结构。"""
    monkeypatch.setattr(storage, "_materials_root", lambda: tmp_path / "materials")
    from app.api.materials import _relative_subtitle_path

    # tmp_path 的父目录必然在素材根之外；文件无需真实存在（relative_to 先抛 ValueError）
    outside = tmp_path.parent / "leak.vtt"
    assert _relative_subtitle_path(str(outside)) == "leak.vtt"

    # 空值安全
    assert _relative_subtitle_path(None) is None


def test_running_row_exposes_progress_fields(client, db_session, tmp_path):
    """运行态字段必须透出，前端才能显示进度条与切片计数。"""
    course_dir = tmp_path / "materials" / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    _add(db_session, "c1", subtitle_status="generating")

    st = whisper_service.TaskState(course_id="c1", status="generating")
    st.progress = 0.4
    st.slices_done = 18
    st.slices_total = 45
    st.phase = "transcribing"
    st.started_at = 1700000000.0
    st.queue_position = 2
    whisper_service._tasks["c1"] = st

    row = _row(client.get("/api/materials", headers=_h("admin", 1)).json(), "c1")

    assert row["subtitle_progress"] == 0.4
    assert row["subtitle_slices_done"] == 18
    assert row["subtitle_slices_total"] == 45
    assert row["subtitle_phase"] == "transcribing"
    assert row["subtitle_started_at"] == 1700000000.0
    assert row["subtitle_queue_position"] == 2


def test_non_running_row_zeroed_and_no_task_state_created(client, db_session, tmp_path):
    """ready 行给零值，且列表查询不该凭空造 TaskState（会灌水 active_task_count）。"""
    course_dir = tmp_path / "materials" / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    _add(db_session, "c1", subtitle_status="ready")

    before = len(whisper_service._tasks)
    row = _row(client.get("/api/materials", headers=_h("admin", 1)).json(), "c1")

    assert row["subtitle_progress"] == 0.0
    assert row["subtitle_slices_done"] == 0
    assert row["subtitle_slices_total"] == 0
    assert row["subtitle_phase"] is None
    assert row["subtitle_started_at"] is None
    assert row["subtitle_queue_position"] == 0
    assert len(whisper_service._tasks) == before, "列表查询不该造 TaskState"


def test_subtitle_status_response_uses_task_prefix(client, db_session, tmp_path):
    """status 端点：内存字段统一 task_ 前缀，不再与 DB 的 subtitle_status 撞名。"""
    course_dir = tmp_path / "materials" / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    _add(db_session, "c1", subtitle_status="generating")

    st = whisper_service.TaskState(course_id="c1", status="generating")
    st.progress = 0.5
    st.slices_done = 10
    st.slices_total = 20
    st.error = None
    whisper_service._tasks["c1"] = st

    body = client.get("/api/materials/c1/subtitle-status", headers=_h("admin", 1)).json()

    assert body["subtitle_status"] == "generating"   # DB 真值
    assert body["task_status"] == "generating"       # 内存 worker
    assert body["task_progress"] == 0.5
    assert body["task_slices_done"] == 10
    assert body["task_slices_total"] == 20
    assert body["queue_position"] == 0


def test_subtitle_status_task_error_hidden_from_user(client, db_session):
    """单查端点的 `task_error` 与列表端点同一规则：仅 admin 可见（PRD §5.5A.7）。

    v9 修的信息泄露：列表端点（`subtitle_error`）过滤了，单查端点漏了，
    而它只用 `get_current_user` 鉴权 —— 任何登录用户都能拿到 worker 的 `str(e)`，
    里面常含服务器绝对路径与异常原文。
    """
    _add(db_session, "c1", subtitle_status="error")

    st = whisper_service.TaskState(course_id="c1", status="error")
    st.error = r"C:\secret\internal\path 炸了"
    whisper_service._tasks["c1"] = st

    admin_body = client.get("/api/materials/c1/subtitle-status", headers=_h("admin", 1)).json()
    assert admin_body["task_error"] == r"C:\secret\internal\path 炸了"

    user_body = client.get("/api/materials/c1/subtitle-status", headers=_h("user", 2)).json()
    assert user_body["task_error"] is None, "task_error 含内部路径，user 角色必须恒为 None"


def test_subtitle_status_does_not_inflate_active_task_count(client, db_session):
    """对未入队的课程请求单查，不得凭空造 TaskState 灌水 `active_task_count()`。

    v9 修：原先单查用 `get_status()`，它会为任意 course_id 造一个 PENDING TaskState。
    于是任何登录用户轮询一次，仪表盘的"活跃任务数"就永久 +1，`_tasks` 无上限增长。
    """
    _add(db_session, "c2")   # 课程存在，但从未入队

    before = whisper_service.active_task_count()
    assert whisper_service.peek_status("c2") is None

    resp = client.get("/api/materials/c2/subtitle-status", headers=_h("user", 2))
    assert resp.status_code == 200
    # 任务不存在时用零值快照兜底，不造假也不污染
    assert resp.json()["task_status"] == "pending"
    assert resp.json()["task_slices_total"] == 0

    assert whisper_service.active_task_count() == before, "单查不得凭空造 TaskState"
    assert whisper_service.peek_status("c2") is None, "peek 仍应返回 None"


# ---------- v9 S1：D3 orphan 自愈 / D4 文件存在性 / task_active ----------


def test_list_exposes_task_active_and_has_file(client, db_session, tmp_path):
    """列表端点必须暴露两个**实时派生**字段，前端靠它们避免两类假象。

    - `subtitle_task_active`：runtime 里是否有真实的未完成任务。
      cancel 模式只让它为 true 的行可选，否则管理员会勾到一行永远取消不掉的素材。
    - `subtitle_has_file`：字幕文件是否**真的在磁盘上**。`subtitle_filename` 只是历史记录，
      文件被手工删掉后它仍有值 —— 用它判断存在性会给出"点开才报错"的假入口（PRD §5.5A.7 / AC-2）。
    """
    # c1：ready + 文件真实存在
    course_dir = tmp_path / "materials" / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    vtt = course_dir / "v.whisper.vtt"
    vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n", encoding="utf-8")
    _add(db_session, "c1", subtitle_status="ready", subtitle_path=str(vtt))

    # c2：ready，但字幕文件已被手工删除（subtitle_path 仍有值）
    _add(
        db_session, "c2",
        subtitle_status="ready",
        subtitle_path=str(tmp_path / "materials" / "c2" / "gone.vtt"),
    )

    # c3：generating，且 runtime 真有任务
    _add(db_session, "c3", subtitle_status="generating")
    whisper_service._tasks["c3"] = whisper_service.TaskState(
        course_id="c3", status=whisper_service.GENERATING
    )

    rows = client.get("/api/materials", headers=_h("admin", 1)).json()

    assert _row(rows, "c1")["subtitle_has_file"] is True, "文件在磁盘上"
    assert _row(rows, "c2")["subtitle_has_file"] is False, (
        "文件已被删 → 必须 false，否则前端会给出点开才报错的假入口"
    )
    assert _row(rows, "c3")["subtitle_task_active"] is True
    # c1/c2 从未入队，不该有活跃任务
    assert _row(rows, "c1")["subtitle_task_active"] is False
    assert _row(rows, "c2")["subtitle_task_active"] is False


def test_orphan_generating_is_healed_to_pending(client, db_session):
    """D3 第二层：DB 写着 generating 但 runtime 无任务 → 列表查询把它修回 pending（AC-16）。

    这是**非法状态**（进程被杀 / worker 崩了），7 个展示态里没有它的位置，
    也不允许为它新增第 8 态 —— 正确做法是修好它，而不是展示它。
    不修的后果：前端对着一个永远不推进的进度条空轮询到进程重启。
    """
    _add(db_session, "c1", subtitle_status="generating", subtitle_error="上次残留的错误")
    assert whisper_service.task_is_active("c1") is False, "前置：runtime 里没有任务"

    rows = client.get("/api/materials", headers=_h("admin", 1)).json()
    row = _row(rows, "c1")
    assert row["subtitle_status"] == "pending", "orphan 必须被修回 pending"
    assert row["subtitle_error"] is None, "残留的失败文案要一并清掉"
    assert row["subtitle_task_active"] is False

    # 断言的是**数据库真的变了**，不只是响应里改了个字段
    db_session.expire_all()
    m = db_session.query(Material).filter(Material.course_id == "c1").one()
    assert m.subtitle_status == "pending"


def test_orphan_healing_is_idempotent(client, db_session, caplog):
    """D3 硬约束 ② 幂等 + ③ 有审计日志：恢复后重复查询不再触发，且触发时留下记录。

    不幂等的自愈会让每次列表查询都写一次库 —— 轮询每 3 秒一次，
    那就是一条持续的写负载，而且任何并发写都会被它覆盖。
    """
    _add(db_session, "c1", subtitle_status="generating")

    with caplog.at_level("WARNING", logger="app.api.materials"):
        client.get("/api/materials", headers=_h("admin", 1))
        assert any("[subtitle-orphan]" in r.getMessage() for r in caplog.records), (
            "自愈必须留审计日志，否则『这行怎么自己变了』无从追溯"
        )
        caplog.clear()
        # 第二次查询：DB 已是 pending，不该再触发
        client.get("/api/materials", headers=_h("admin", 1))
        assert not any("[subtitle-orphan]" in r.getMessage() for r in caplog.records), (
            "自愈必须幂等：已恢复的行不该被反复修复"
        )


def test_normal_get_produces_no_state_change(client, db_session, monkeypatch, tmp_path):
    """AC-17：没有 orphan 时，`GET /api/materials` 一次写都不发生。

    D3 允许在 GET 里写库，**但仅限** orphan（generating + 无 runtime 任务）这一条路径。
    这条测试把边界钉死：一旦有人把自愈扩展到「ready + 文件缺失也顺便修一下」，
    这里会立刻变红 —— 那就越过了 D3 与 D4 的分界（后者明确交给 rescan，普通 GET 纯读）。
    """
    course_dir = tmp_path / "materials" / "c1"
    course_dir.mkdir(parents=True, exist_ok=True)
    vtt = course_dir / "v.whisper.vtt"
    vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n", encoding="utf-8")
    # 覆盖三种"正常"状态：ready / pending / error，外加一个 ready 但文件缺失
    _add(db_session, "c1", subtitle_status="ready", subtitle_path=str(vtt))
    _add(db_session, "c2", subtitle_status="pending")
    _add(
        db_session, "c3",
        subtitle_status="error",
        subtitle_error="上次生成炸了",
    )
    _add(
        db_session, "c4",
        subtitle_status="ready",
        subtitle_path=str(tmp_path / "materials" / "c4" / "gone.vtt"),  # ready 但文件没了
        review_state="reviewed",
    )

    def _snapshot():
        db_session.expire_all()
        return {
            m.course_id: (m.subtitle_status, m.subtitle_error, m.review_state)
            for m in db_session.query(Material).all()
        }

    before = _snapshot()

    commits: list[int] = []
    orig_commit = db_session.commit
    monkeypatch.setattr(db_session, "commit", lambda: commits.append(1) or orig_commit())

    for _ in range(3):
        resp = client.get("/api/materials", headers=_h("admin", 1))
        assert resp.status_code == 200

    assert commits == [], f"正常 GET 不该产生任何写操作，实际 commit {len(commits)} 次"
    assert _snapshot() == before, "GET 前后所有行的状态快照必须完全一致"
