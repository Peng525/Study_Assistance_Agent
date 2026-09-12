"""v8 §5.5A.5 批量端点测试（生成 / 取消 / 审核）+ 状态链路 root-cause 回归。

守两条防线：

1. **后端二次校验**：前端按白名单过滤 ID，后端必须用**同一份**白名单再校验一次。
   即使收到非法状态的 course_id，也要在该条返回 `ok=false` + 原因，
   而不是把它重复塞进队列（那会让一个已 ready 的字幕被重新转写，
   且已审核结论被悄悄冲掉）。
2. **路由顺序回归**：`/batch/*` 必须注册在 `/{course_id}/*` 之前，否则会被
   `/{course_id}/generate-subtitle` 当成 `course_id="batch"` 命中返回 404。

另外守住 v8 修的 root-cause：手动触发生成后 DB 必须真的进入 `generating`。
"""

import pytest

from app.models.models import Material
from app.services import storage, whisper_service
from tests.test_admin_materials import _h, client  # noqa: F401 — 复用同名 fixture 与 helper


@pytest.fixture(autouse=True)
def _isolate_whisper(monkeypatch):
    """不真起 worker 线程，也不依赖系统里是否装了 ffmpeg。"""
    monkeypatch.setattr(whisper_service, "is_ffmpeg_available", lambda: True)
    monkeypatch.setattr(whisper_service, "_start_worker", lambda: None)
    yield
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()


def _mk(db, course_id, *, has_video=True, subtitle_status="pending", review_state="unreviewed", **kw):
    """造一条素材。has_video=False 用于触发"无视频文件"这类 per-item 失败。

    `**kw` 透传给 `Material(...)`，用于构造 `subtitle_source=...` 这类特定字段组合。
    """
    course_dir = storage._materials_root() / course_id
    course_dir.mkdir(parents=True, exist_ok=True)
    material = Material(
        course_id=course_id,
        dir_path=str(course_dir),
        status="ready",
        subtitle_status=subtitle_status,
        review_state=review_state,
        **kw,
    )
    if has_video:
        video = course_dir / "v.mp4"
        video.write_bytes(b"\x00" * 16)
        material.video_path = str(video)
    db.add(material)
    db.commit()
    return material


def _by_id(results):
    return {r["course_id"]: r for r in results}


# ---------- 路由顺序回归（最高优先级） ----------


def test_batch_route_not_swallowed_by_course_id(client, db_session):
    """回归闸门：/batch/* 必须注册在 /{course_id}/* 之前。

    顺序错了的话，"/batch/generate-subtitle" 会被 "/{course_id}/generate-subtitle"
    当成 course_id="batch" 命中 → 404「课程不存在」。症状极像"端点忘了写"，
    实际只是注册顺序问题，排查成本很高，所以必须有这条守住。
    """
    _mk(db_session, "c1", subtitle_status="pending")
    resp = client.post(
        "/api/admin/materials/batch/generate-subtitle",
        json={"course_ids": ["c1"]},
        headers=_h(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"][0]["course_id"] == "c1"
    assert body["results"][0]["ok"] is True


# ---------- 批量生成 ----------


def test_batch_generate_all_succeeded(client, db_session):
    _mk(db_session, "c1", subtitle_status="pending")
    _mk(db_session, "c2", subtitle_status="error")

    resp = client.post(
        "/api/admin/materials/batch/generate-subtitle",
        json={"course_ids": ["c1", "c2"]},
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 0

    for course_id in ("c1", "c2"):
        material = db_session.query(Material).filter_by(course_id=course_id).one()
        assert material.subtitle_status == "generating"
        assert material.subtitle_source is None, "入队尚未生成新字幕，不应提前声明来源"
        assert material.subtitle_error is None


def test_batch_generate_rejects_illegal_states(client, db_session):
    """后端二次校验：ready / generating / 无视频的行即使被传进来也要拒绝。"""
    _mk(db_session, "c1", subtitle_status="ready", review_state="reviewed")
    _mk(db_session, "c2", subtitle_status="generating")
    _mk(db_session, "c3", subtitle_status="pending")
    _mk(db_session, "c4", has_video=False, subtitle_status="error")
    # "ghost" 故意不创建，用于覆盖"课程不存在"分支

    resp = client.post(
        "/api/admin/materials/batch/generate-subtitle",
        json={"course_ids": ["c1", "c2", "c3", "c4", "ghost"]},
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 1        # 只有 c3
    assert body["failed"] == 4

    rows = _by_id(body["results"])
    assert rows["c3"]["ok"] is True
    for course_id in ("c1", "c2", "c4", "ghost"):
        assert rows[course_id]["ok"] is False
        assert rows[course_id]["error"]

    assert "不可生成字幕" in rows["c1"]["error"]
    assert "不可生成字幕" in rows["c2"]["error"]
    assert "无视频文件" in rows["c4"]["error"]
    assert "课程不存在" in rows["ghost"]["error"]

    # 被拒绝的行必须原样保留，尤其已审核结论不能被顺手冲掉
    c1 = db_session.query(Material).filter_by(course_id="c1").one()
    assert c1.subtitle_status == "ready"
    assert c1.review_state == "reviewed"
    assert db_session.query(Material).filter_by(course_id="c2").one().subtitle_status == "generating"


def test_batch_generate_rejected_without_ffmpeg(client, db_session, monkeypatch):
    """ffmpeg 不可用属整批前置条件，直接 400，不进 per-item 结果。"""
    _mk(db_session, "c1", subtitle_status="pending")
    monkeypatch.setattr(whisper_service, "is_ffmpeg_available", lambda: False)

    resp = client.post(
        "/api/admin/materials/batch/generate-subtitle",
        json={"course_ids": ["c1"]},
        headers=_h(),
    )
    assert resp.status_code == 400
    assert "ffmpeg" in resp.json()["detail"]
    # 请求被拒，DB 不该被改动
    assert db_session.query(Material).filter_by(course_id="c1").one().subtitle_status == "pending"


# ---------- 批量取消 ----------


def test_batch_cancel_succeeded(client, db_session):
    for course_id in ("c1", "c2"):
        material = _mk(db_session, course_id, subtitle_status="pending")
        whisper_service.enqueue(course_id, material.video_path)
        material.subtitle_status = "generating"
    db_session.commit()

    resp = client.post(
        "/api/admin/materials/batch/cancel-subtitle",
        json={"course_ids": ["c1", "c2"]},
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 0


def test_batch_cancel_skips_non_cancellable(client, db_session):
    """ready 的行没有任务可取消，必须在该条报 ok=false。

    ⚠️ c1 必须**真的入队**才有"可取消的任务"——只把 DB 改成 generating 是不够的。
    本用例早期版本就是没入队也能过，那是在给 `get_status()` 凭空造 TaskState 的 bug 打掩护。
    """
    m1 = _mk(db_session, "c1", subtitle_status="pending")
    whisper_service.enqueue("c1", m1.video_path)
    m1.subtitle_status = "generating"
    db_session.commit()

    _mk(db_session, "c2", subtitle_status="ready", review_state="reviewed")

    resp = client.post(
        "/api/admin/materials/batch/cancel-subtitle",
        json={"course_ids": ["c1", "c2"]},
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1

    rows = _by_id(body["results"])
    assert rows["c1"]["ok"] is True
    assert "不可取消" in rows["c2"]["error"]
    # ready 行的审核结论不能被取消操作影响
    c2 = db_session.query(Material).filter_by(course_id="c2").one()
    assert c2.subtitle_status == "ready"
    assert c2.review_state == "reviewed"


def test_batch_cancel_all_failed(client, db_session):
    for course_id in ("c1", "c2"):
        _mk(db_session, course_id, subtitle_status="ready", review_state="reviewed")

    resp = client.post(
        "/api/admin/materials/batch/cancel-subtitle",
        json={"course_ids": ["c1", "c2"]},
        headers=_h(),
    )
    body = resp.json()
    assert body["succeeded"] == 0
    assert body["failed"] == 2


def test_batch_cancel_without_real_runtime_task_is_not_a_false_success(client, db_session):
    """DB 写着 generating 但 runtime 里根本没有任务（orphan）→ 必须 ok=false。

    典型成因是进程重启：DB 的 `generating` 只是一个"意图"，重启后它指向一个不存在的任务。
    **没有 runtime 任务就没有东西可取消** —— 必须报告失败，而不是假装成功。
    假成功是最难排查的一类事故：管理员点了取消、UI 说"已取消"、任务其实还在跑。

    守的是两件事：
    1. `cancel()` 返回 `None` 时调用方必须报错（旧实现丢弃返回值，一律当成功）；
    2. 状态读取用**无副作用**的 `peek_status()` —— 曾用 `get_status()` 会为陌生 course_id
       凭空造一个 PENDING TaskState，使"从没排过队"的行也能通过白名单。

    （对应 AC-20）
    """
    _mk(db_session, "c1", subtitle_status="generating")
    before_tasks = len(whisper_service._tasks)

    resp = client.post(
        "/api/admin/materials/batch/cancel-subtitle",
        json={"course_ids": ["c1"]},
        headers=_h(),
    )
    body = resp.json()

    assert body["succeeded"] == 0, "没有真实任务的行不该报取消成功"
    assert body["failed"] == 1
    assert "队列中无该任务" in body["results"][0]["error"]
    # 关键：不能凭空造出 TaskState
    assert len(whisper_service._tasks) == before_tasks
    assert whisper_service.peek_status("c1") is None


def test_batch_cancel_rejects_pending_status(client, db_session):
    """`pending` 不在 CANCELLABLE 白名单内。

    `pending` 表示**从未排上任务**（无视频 / 缺 ffmpeg / 取消成功 / 孤儿复位），
    内存里根本没有对应的 TaskState，谈不上"取消"。
    旧版白名单是 `("pending", "generating")`，靠 runtime 的 peek 兜底，
    等于准入校验和真实可取消性用了两套标准。现在两者统一：进不了白名单 = 不可取消。
    """
    _mk(db_session, "c1", subtitle_status="pending")

    resp = client.post(
        "/api/admin/materials/batch/cancel-subtitle",
        json={"course_ids": ["c1"]},
        headers=_h(),
    )
    body = resp.json()
    assert body["succeeded"] == 0
    assert "不可取消" in body["results"][0]["error"]
    assert "pending" in body["results"][0]["error"]


# ---------- 批量审核 ----------


def test_batch_review_succeeded(client, db_session):
    for course_id in ("c1", "c2"):
        _mk(db_session, course_id, subtitle_status="ready", review_state="unreviewed")

    resp = client.post(
        "/api/admin/materials/batch/review",
        json={"course_ids": ["c1", "c2"], "review_state": "reviewed"},
        headers=_h(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 2
    for course_id in ("c1", "c2"):
        assert (
            db_session.query(Material).filter_by(course_id=course_id).one().review_state
            == "reviewed"
        )


def test_batch_review_skips_non_ready(client, db_session):
    """未生成完成的字幕不能标记校对状态。"""
    _mk(db_session, "c1", subtitle_status="ready", review_state="unreviewed")
    _mk(db_session, "c2", subtitle_status="error")

    resp = client.post(
        "/api/admin/materials/batch/review",
        json={"course_ids": ["c1", "c2"], "review_state": "reviewed"},
        headers=_h(),
    )
    body = resp.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1

    rows = _by_id(body["results"])
    assert rows["c1"]["ok"] is True
    assert "无法修改校对状态" in rows["c2"]["error"]
    assert db_session.query(Material).filter_by(course_id="c2").one().review_state == "unreviewed"


def test_batch_review_can_undo(client, db_session):
    for course_id in ("c1", "c2"):
        _mk(db_session, course_id, subtitle_status="ready", review_state="reviewed")

    resp = client.post(
        "/api/admin/materials/batch/review",
        json={"course_ids": ["c1", "c2"], "review_state": "unreviewed"},
        headers=_h(),
    )
    body = resp.json()
    assert body["succeeded"] == 2
    for course_id in ("c1", "c2"):
        assert (
            db_session.query(Material).filter_by(course_id=course_id).one().review_state
            == "unreviewed"
        )


def test_batch_review_invalid_state_is_400(client, db_session):
    """review_state 非法是入参错误，不是 per-item 失败 → 整体 400，DB 不动。"""
    _mk(db_session, "c1", subtitle_status="ready", review_state="unreviewed")

    resp = client.post(
        "/api/admin/materials/batch/review",
        json={"course_ids": ["c1"], "review_state": "bogus"},
        headers=_h(),
    )
    assert resp.status_code == 400
    assert db_session.query(Material).filter_by(course_id="c1").one().review_state == "unreviewed"


# ---------- 单条端点：与批量共用同一套白名单 + root-cause 回归 ----------


def test_single_generate_writes_generating_not_pending(client, db_session):
    """B1 root-cause 回归闸门：DB 必须真的进入 generating。

    v8 之前这里恒写 pending（因为 enqueue() 对新任务返回 PENDING，worker 还没启动），
    后果是前端轮询不启动、进度条不渲染、按钮态不变化 —— 整条进度链路形同虚设。
    """
    _mk(db_session, "c1", subtitle_status="pending")

    resp = client.post("/api/admin/materials/c1/generate-subtitle", headers=_h())
    assert resp.status_code == 200
    material = db_session.query(Material).filter_by(course_id="c1").one()
    assert material.subtitle_status == "generating"
    assert material.subtitle_source is None, "入队尚未生成新字幕，不应提前声明来源"


def test_single_generate_after_error_clears_subtitle_error(client, db_session):
    """失败后重试时，上一次的错误文案必须清掉，否则 UI 会一直显示旧失败原因。"""
    material = _mk(db_session, "c1", subtitle_status="error")
    material.subtitle_error = "上一次转写炸了"
    db_session.commit()

    resp = client.post("/api/admin/materials/c1/generate-subtitle", headers=_h())
    assert resp.status_code == 200
    material = db_session.query(Material).filter_by(course_id="c1").one()
    assert material.subtitle_status == "generating"
    assert material.subtitle_error is None


def test_single_generate_rejects_ready_state(client, db_session):
    """单条端点与批量共用同一套白名单 —— v8 之前单条可以对 ready 重复触发生成。"""
    _mk(db_session, "c1", subtitle_status="ready", review_state="reviewed")

    resp = client.post("/api/admin/materials/c1/generate-subtitle", headers=_h())
    assert resp.status_code == 400
    assert "不可生成字幕" in resp.json()["detail"]


def test_single_cancel_rejects_non_cancellable(client, db_session):
    _mk(db_session, "c1", subtitle_status="ready", review_state="reviewed")

    resp = client.post("/api/admin/materials/c1/cancel-subtitle", headers=_h())
    assert resp.status_code == 400
    assert "不可取消" in resp.json()["detail"]


# ---------- v9：取消语义与来源判定（PRD §5.5A.3 / AC-13 / AC-14） ----------


@pytest.fixture()
def isolated_materials_root(monkeypatch, tmp_path):
    """把素材根指到 tmp。

    真实的 `materials/` 目录是**跨测试共享**的：多个用例往同一个 course_id 目录
    里放文件会互相污染 —— `scan_course_dir` 用 `iterdir()` 挑第一个字幕，
    而 NTFS 不保证顺序，测试就会随机红。
    """
    root = tmp_path / "materials"
    root.mkdir()
    monkeypatch.setattr(storage, "_materials_root", lambda: root)
    return root


def test_cancel_while_queued_converges_db_to_pending(client, db_session):
    """排队中的任务被取消后，DB 必须收敛为 **pending + error=None**，而不是永远停在 generating。

    ⚠️ v9 修的真 bug：`cancel()` 只把任务移出 `_queue`，而 `_worker_loop` 只处理
    从 `_queue[0]` 取到的 course_id —— 已出队的任务**永远不会被 worker 碰**，
    也就没有任何人会调 `_write_back_to_db`。请求方不自己收尾的话，该行会一直
    显示"生成中"并每 3 秒空轮询一次，直到进程重启被 seed 复位（AC-13）。

    断言 `pending` 而不是 `error`：取消是"这次没生成、随时可以再来"，
    不是"这个素材生成不了"。写 `error` 会让管理员误判素材有问题（PRD §5.5A.3）。
    """
    _mk(db_session, "c1", review_state="reviewed", subtitle_source="manual")
    # fixture 把 _start_worker mock 成 no-op，任务入队后停在"排队中"
    client.post("/api/admin/materials/c1/generate-subtitle", headers=_h())
    db_session.expire_all()
    assert db_session.query(Material).filter_by(course_id="c1").one().subtitle_status == "generating"

    resp = client.post("/api/admin/materials/c1/cancel-subtitle", headers=_h())
    assert resp.status_code == 200

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "pending", "排队中取消必须由请求方自己收尾，不能停在 generating"
    assert m.subtitle_error is None, "取消不是失败，不留失败文案"
    assert m.review_state == "reviewed", "排队中取消没有产生新字幕，不应冲掉原校对状态"
    assert m.subtitle_source == "manual", "排队中取消没有产生新字幕，不应改写旧字幕来源"
    # runtime 必须一并清掉：留着会让 active_task_count() 虚高，
    # 且列表端点的 subtitle_task_active 会把这行显示成仍可取消（鬼影入口）
    assert whisper_service.peek_status("c1") is None
    assert whisper_service.active_task_count() == 0


def test_cancel_generating_writes_pending_not_failed(client, db_session, monkeypatch):
    """生成中取消：worker 捕获 `_CancelledError` 后写回 **pending + error=None**。

    端到端跑真实 worker loop 与真实 `_write_back_to_db`，只 mock 转写这一步 ——
    避免测试断言的只是"函数被调用了"而不是"DB 真的变了"。
    """
    _mk(db_session, "c1", review_state="reviewed", subtitle_source="manual")
    client.post("/api/admin/materials/c1/generate-subtitle", headers=_h())

    def raise_cancelled(*_a, **_kw):
        raise whisper_service._CancelledError("c1")

    monkeypatch.setattr(whisper_service, "_run_whisper", raise_cancelled)
    # _write_back_to_db 用**独立** SessionLocal（worker 线程不复用请求 session），
    # 默认指向真实库；这里换成测试库，否则断言查的是两个不同的数据库。
    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: db_session)
    whisper_service._worker_loop()

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "pending", "取消后回到待生成，不是 error"
    assert m.subtitle_error is None, "取消不是失败，不留失败文案（也不留「已取消」标记）"
    assert m.review_state == "reviewed", "生成中取消没有写回新字幕，不应冲掉原校对状态"
    assert m.subtitle_source == "manual", "生成中取消没有写回新字幕，不应改写旧字幕来源"
    assert "c1" not in whisper_service._cancel_requested, (
        "收尾必须清掉取消标记，否则同一个素材的下一次任务一开始就『被取消』"
    )


def test_real_failure_is_not_treated_as_cancel(client, db_session, monkeypatch):
    """真失败必须写 error + 失败详情，**不能**被当成取消（反向回归）。

    管理员必须能区分「跑挂了」（有失败详情可查、值得重试）和「我自己停的」。
    取消回 pending、失败回 error —— 这两条路径的唯一区别就在这里。
    """
    _mk(db_session, "c1", review_state="reviewed", subtitle_source="manual")
    client.post("/api/admin/materials/c1/generate-subtitle", headers=_h())

    def boom(*_a, **_kw):
        raise RuntimeError("boom 转写炸了")

    monkeypatch.setattr(whisper_service, "_run_whisper", boom)
    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: db_session)
    whisper_service._worker_loop()

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "error", "真失败不能回到 pending（那会让管理员以为只是没生成）"
    assert "boom" in (m.subtitle_error or "")
    assert m.review_state == "reviewed", "生成失败没有替换字幕内容，不应冲掉原校对状态"
    assert m.subtitle_source == "manual", "生成失败没有替换字幕内容，不应改写旧字幕来源"


def test_regenerate_resets_review_state_only_after_success(client, db_session, monkeypatch):
    """入队保留旧校对状态；新字幕成功写回后才标为 unreviewed。"""
    _mk(
        db_session,
        "c1",
        subtitle_status="error",
        review_state="reviewed",
        subtitle_source="manual",
    )

    resp = client.post(
        "/api/admin/materials/batch/generate-subtitle",
        json={"course_ids": ["c1"]},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded"] == 1

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "generating"
    assert m.review_state == "reviewed", "任务尚未成功，不应提前冲掉旧校对状态"
    assert m.subtitle_source == "manual", "任务尚未成功，不应提前改写旧字幕来源"

    monkeypatch.setattr(whisper_service, "_run_whisper", lambda *_a, **_kw: "generated.vtt")
    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: db_session)
    whisper_service._worker_loop()

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "ready"
    assert m.review_state == "unreviewed", "成功生成的新字幕必须重新进入未校对状态"
    assert m.subtitle_source == "whisper", "成功生成的新字幕必须标记为 AI 自动转写来源"


def test_rescan_keeps_whisper_source(db_session, isolated_materials_root):
    """重新扫描后，Whisper 生成的 `*.whisper.vtt` 仍标为 whisper（AC-14）。

    原实现无条件写 manual —— 一次重新扫描就把全部机器生成字幕改标成"人工上传"，
    会让管理员误判字幕来源并跳过必要的质量抽查。
    """
    from app.api.admin_materials import _rescan_material

    material = _mk(db_session, "c1")
    vtt = isolated_materials_root / "c1" / "v.whisper.vtt"
    vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n你好\n", encoding="utf-8")

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "ready"
    assert m.subtitle_source == "whisper", "机器生成的字幕不得被改标成人工上传"


def test_rescan_preserves_existing_source(db_session, isolated_materials_root):
    """B5 的核心：**已有来源不得被文件名覆盖**（PRD §5.5A.7）。

    构造一个"来源已记为 whisper、但文件名不像 whisper 产物"的行。
    旧实现无条件写 `_infer_subtitle_source(path)`，这里会被改标成 manual。
    反过来也一样：来源记为 manual 而文件名是 `*.whisper.vtt` 时，也不许改成 whisper。
    """
    from app.api.admin_materials import _rescan_material

    material = _mk(db_session, "c1", subtitle_source="whisper")
    # 文件名会让 infer 判成 manual
    vtt = isolated_materials_root / "c1" / "subtitle_abc.vtt"
    vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n你好\n", encoding="utf-8")

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_source == "whisper", "扫描不得按文件名改写已有 provenance"

    # 反向：来源 manual + 文件名像 whisper 产物，同样不许改
    material2 = _mk(db_session, "c2", subtitle_source="manual")
    vtt2 = isolated_materials_root / "c2" / "v.whisper.vtt"
    vtt2.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n人工\n", encoding="utf-8")
    _rescan_material(db_session, material2)

    db_session.expire_all()
    m2 = db_session.query(Material).filter_by(course_id="c2").one()
    assert m2.subtitle_source == "manual"


def test_rescan_infers_source_only_when_missing(db_session, isolated_materials_root):
    """`_infer_subtitle_source()` 只作 legacy fallback：仅在来源缺失时兜底。"""
    from app.api.admin_materials import _rescan_material

    material = _mk(db_session, "c1", subtitle_source=None)
    vtt = isolated_materials_root / "c1" / "v.whisper.vtt"
    vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n你好\n", encoding="utf-8")

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_source == "whisper", "老数据（source 为 None）应被兜底推断"


def test_rescan_marks_manual_source(db_session, isolated_materials_root):
    """人工上传的字幕（非 `.whisper.vtt` 命名）仍标为 manual。"""
    from app.api.admin_materials import _rescan_material

    material = _mk(db_session, "c1")
    vtt = isolated_materials_root / "c1" / "subtitle_abc.vtt"
    vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n人工字幕\n", encoding="utf-8")

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_source == "manual"


def test_infer_subtitle_source_prefers_whisper_on_unknown():
    """来源判定偏向标成 whisper。

    误标成「人工上传」→ 管理员以为人已校过而跳过抽查（安全风险）；
    误标成「Whisper」→ 只是显示不准。**两害相权取其轻。**
    """
    from app.api.admin_materials import _infer_subtitle_source

    assert _infer_subtitle_source("/x/y/video.whisper.vtt") == "whisper"
    assert _infer_subtitle_source("/x/y/subtitle_abc.vtt") == "manual"
    assert _infer_subtitle_source(None) == "whisper"


# ---------- v9 批次 2 · B2-1：best-effort 的两条硬约束 ----------


def test_batch_commits_each_item(client, db_session, monkeypatch):
    """每条执行后立即 commit，不攒到循环末尾（PRD §5.5A.5）。

    攒到末尾时若这次 commit 失败（或中途异常），**已经 enqueue 出去的线程仍在跑**，
    之后会用独立 SessionLocal 把 DB 写回 ready —— 结果是「HTTP 说失败、字幕最后却变成
    ready」，响应与事实分叉，管理员会重复点生成。
    """
    _mk(db_session, "c1", subtitle_status="pending")
    _mk(db_session, "c2", subtitle_status="pending")

    commits: list[int] = []
    orig_commit = db_session.commit
    monkeypatch.setattr(db_session, "commit", lambda: commits.append(1) or orig_commit())

    resp = client.post(
        "/api/admin/materials/batch/generate-subtitle",
        json={"course_ids": ["c1", "c2"]},
        headers=_h(),
    )
    assert resp.status_code == 200, resp.text
    assert len(commits) == 2, f"两条素材应 commit 两次（逐条），实际 {len(commits)} 次"


def test_batch_survives_unexpected_exception(client, db_session, monkeypatch):
    """单条抛出**非 ValueError** 的内部错误不得中断整个循环（PRD §5.5A.5）。

    只捕获 ValueError 时，IO / DB 错误会让剩余 ID 既不执行也不出现在结果里，
    且整批返回 HTTP 500 —— 与「HTTP 200 + per-item」的契约直接冲突。
    """
    from app.api import admin_materials as am

    for cid in ("c1", "c2", "c3"):
        _mk(db_session, cid, subtitle_status="pending")

    orig = am._do_generate_subtitle

    def flaky(material):
        if material.course_id == "c2":
            raise RuntimeError("模拟内部错误（非 ValueError）")
        return orig(material)

    monkeypatch.setattr(am, "_do_generate_subtitle", flaky)

    resp = client.post(
        "/api/admin/materials/batch/generate-subtitle",
        json={"course_ids": ["c1", "c2", "c3"]},
        headers=_h(),
    )
    assert resp.status_code == 200, "整批不能因为单条异常而变 500"
    body = resp.json()
    assert [r["course_id"] for r in body["results"]] == ["c1", "c2", "c3"], "每条都必须出现在 results 里"

    by = _by_id(body["results"])
    assert by["c1"]["ok"] is True
    assert by["c2"]["ok"] is False and "模拟内部错误" in by["c2"]["error"]
    assert by["c3"]["ok"] is True, "c2 炸了不能连累 c3"


def test_later_failure_does_not_discard_earlier_success(client, db_session, monkeypatch, tmp_path):
    """后续条目失败不能把前面已成功的结果一起回滚。

    `rollback` 必须只回滚失败的那一条自己。若整批回滚，已入队的后台线程却还在跑，
    DB 说没在生成、线程却在跑 —— 管理员会以为失败了而重复点生成。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api import admin_materials as am

    _mk(db_session, "c1", subtitle_status="pending")
    _mk(db_session, "c2", subtitle_status="pending")

    orig = am._do_generate_subtitle

    def flaky(material):
        if material.course_id == "c2":
            raise RuntimeError("模拟内部错误")
        return orig(material)

    monkeypatch.setattr(am, "_do_generate_subtitle", flaky)

    resp = client.post(
        "/api/admin/materials/batch/generate-subtitle",
        json={"course_ids": ["c1", "c2"]},
        headers=_h(),
    )
    assert resp.status_code == 200

    # 用**独立 session** 读，避免读到原 session 里未提交的缓存
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    s2 = sessionmaker(bind=engine)()
    try:
        m1 = s2.query(Material).filter(Material.course_id == "c1").first()
        assert m1 is not None and m1.subtitle_status == "generating", (
            f"c1 已成功入队，DB 应保留 generating，实际 {m1 and m1.subtitle_status}"
        )
    finally:
        s2.close()


# ---------- v9 S1 · D2：rescan 对审核结论的影响 ----------


def _write_vtt(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n{text}\n", encoding="utf-8"
    )


def test_rescan_does_not_infer_calibration_when_subtitle_replaced(db_session, isolated_materials_root):
    """Rescan 只同步磁盘事实，不推断字幕是否经过人工校对。"""
    from app.api.admin_materials import _rescan_material

    old_vtt = isolated_materials_root / "c1" / "subtitle_old.vtt"
    _write_vtt(old_vtt, "原来人工校过的字幕")
    material = _mk(db_session, "c1", subtitle_status="ready", review_state="reviewed")
    material.subtitle_path = str(old_vtt)
    db_session.commit()

    # 管理员换了一个字幕文件（旧的删掉、放上新的）
    old_vtt.unlink()
    new_vtt = isolated_materials_root / "c1" / "v.whisper.vtt"
    _write_vtt(new_vtt, "全新的机器转写内容")

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "ready"
    assert m.review_state == "reviewed"


def test_rescan_keeps_review_when_content_unchanged(db_session, isolated_materials_root):
    """Rescan 不拥有校对生命周期，扫描本身必须保留人工校对状态。"""
    from app.api.admin_materials import _rescan_material

    vtt = isolated_materials_root / "c1" / "v.whisper.vtt"
    _write_vtt(vtt, "一直没变的字幕")
    material = _mk(db_session, "c1", subtitle_status="ready", review_state="reviewed")
    material.subtitle_path = str(vtt)
    db_session.commit()

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.review_state == "reviewed", "重新扫描不应冲掉人工校对状态"
    assert m.scanned_at is not None, "扫描时间仍然要更新（它记录的是扫描行为，不是内容变化）"


def test_rescan_does_not_change_calibration_when_subtitle_removed(db_session, isolated_materials_root):
    """字幕文件缺失会改变生成状态，但 rescan 不拥有校对生命周期。"""
    from app.api.admin_materials import _rescan_material

    vtt = isolated_materials_root / "c1" / "v.whisper.vtt"
    _write_vtt(vtt, "将要被删掉的字幕")
    material = _mk(db_session, "c1", subtitle_status="ready", review_state="reviewed")
    material.subtitle_path = str(vtt)
    db_session.commit()

    vtt.unlink()  # 字幕被手工删除
    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    # B.1 后 rescan 不再自动 enqueue：ready + 文件缺失沿用既定边界落 pending，
    # 不会是 generating（生成必须由 admin 手动点）。
    assert m.subtitle_status == "pending"
    assert m.review_state == "reviewed"


# ---------- v9 S1 · B.1：rescan 不再插手字幕任务生命周期 ----------
#
# 根因：`_rescan_material` 的"无字幕"分支无条件 `enqueue()` + 写 `generating`
# + `source="whisper"`，于是管理员"只是点了重新扫描素材"，3 条失败任务就被重新
# 塞回队列；而且不论之前什么状态都先洗成 `pending` + `subtitle_error=None`，
# 把 error + "File model.bin is incomplete..." 这类真实失败现场抹掉，事后无从定位。
#
# B.1 之后：rescan 只重新识别素材 —— 不排任务、不洗 error。


def test_rescan_does_not_enqueue_when_subtitle_missing(db_session, isolated_materials_root):
    """核心：无字幕时 rescan 不得再 enqueue —— 这是"重扫就重跑"的元凶。"""
    from app.api.admin_materials import _rescan_material

    material = _mk(db_session, "c1", subtitle_status="pending")
    db_session.commit()

    _rescan_material(db_session, material)

    assert not whisper_service.task_exists("c1"), "rescan 不得把任务塞回 runtime"
    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status != "generating", "rescan 不得把状态推进到 generating"


def test_rescan_preserves_error_and_error_message(db_session, isolated_materials_root):
    """error 状态必须留住故障现场：状态与失败文案都不许被重扫洗掉。

    真实事故：3 条任务失败后点「重新扫描素材」，`subtitle_error` 里的
    "File model.bin is incomplete..." 被清空、状态洗成 pending，事后无从定位。
    """
    from app.api.admin_materials import _rescan_material

    err = "File model.bin is incomplete: failed to read a value of size 4 at position 0"
    material = _mk(db_session, "c1", subtitle_status="error")
    material.subtitle_error = err
    db_session.commit()

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "error", "重扫不该把 error 洗成 pending"
    assert m.subtitle_error == err, "失败文案必须逐字保留，否则故障现场丢失"


def test_rescan_keeps_pending_status(db_session, isolated_materials_root):
    """pending 保持 pending（不因为重扫而推进或倒退）。"""
    from app.api.admin_materials import _rescan_material

    material = _mk(db_session, "c1", subtitle_status="pending")
    db_session.commit()

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "pending"


def test_rescan_leaves_orphan_generating_to_d3(db_session, isolated_materials_root):
    """DB=generating 但 runtime 无任务（orphan）：rescan 不抢，留给 D3 自愈。

    若 rescan 在这里自己写 pending，D3 的 orphan 检测就永远看不到
    "DB=generating + 无 runtime" 这个组合，那层自愈形同虚设。
    """
    from app.api.admin_materials import _rescan_material

    material = _mk(db_session, "c1", subtitle_status="generating")
    db_session.commit()
    # _isolate_whisper 已清空 _tasks，runtime 里没有这个任务 = orphan

    _rescan_material(db_session, material)

    db_session.expire_all()
    m = db_session.query(Material).filter_by(course_id="c1").one()
    assert m.subtitle_status == "generating", "rescan 不该抢 D3 的活，要保持原状等自愈"
    assert not whisper_service.task_exists("c1")
