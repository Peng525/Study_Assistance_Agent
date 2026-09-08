"""v8 §5.5A.4 字幕生成细粒度进度测试。

覆盖：
- `TaskState` 外泄 `slices_done` / `slices_total` / `phase` / `started_at`
  （前端据此显示 "生成中 43% / ███████░░░ / 18 / 45"）
- `peek_status()` 只读，绝不凭空造 TaskState —— 这是列表端点不灌水
  `active_task_count()` 的唯一保障
- `enqueue()` 返回真实排队位次（前端显示"排队中（第 N 位）"）
- 重新入队清空上一轮切片进度（否则前端会看到残留的 18/45）
"""

import pytest

from app.services import whisper_service
from app.services.whisper_service import TaskState


@pytest.fixture(autouse=True)
def reset_state():
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()
    yield
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()


def test_get_status_exposes_slice_progress():
    """进度不能只有一个百分比：前端要显示 "18 / 45" 和阶段，还得有降级用的 started_at。"""
    st = TaskState(course_id="c1", status="generating", progress=0.4)
    st.slices_done = 18
    st.slices_total = 45
    st.phase = "transcribing"
    st.started_at = 1700000000.0
    whisper_service._tasks["c1"] = st

    snap = whisper_service.peek_status("c1")
    assert snap["slices_done"] == 18
    assert snap["slices_total"] == 45
    assert snap["phase"] == "transcribing"
    assert snap["started_at"] == 1700000000.0
    assert snap["progress"] == 0.4


def test_get_status_merging_phase():
    """切片跑完、合并写盘阶段：phase='merging'，前端显示"合并字幕…"。"""
    st = TaskState(course_id="c1", status="generating", progress=1.0)
    st.slices_done = 45
    st.slices_total = 45
    st.phase = "merging"
    whisper_service._tasks["c1"] = st

    snap = whisper_service.peek_status("c1")
    assert snap["phase"] == "merging"
    assert snap["slices_done"] == snap["slices_total"]


def test_peek_status_returns_none_for_unknown_course():
    """peek 是只读的：陌生 course_id 返回 None，不创建 TaskState。"""
    assert whisper_service.peek_status("never-seen") is None
    assert "never-seen" not in whisper_service._tasks


def test_peek_status_does_not_inflate_active_task_count():
    """peek_status 存在的唯一理由：曾有一个 `get_status` 会为任意 course_id 凭空造 PENDING TaskState。

    列表端点对 N 行逐个查询时，用它会把 active_task_count() 灌水，
    /whisper/model-status 的 active_tasks 从此不准，「可取消的行」也会出现鬼影。

    `get_status` 现已**删除**（靠文档约束"列表场景要用 peek"是守不住的，新函数照样用错），
    所以这里不再需要"反证"分支 —— **本模块已不存在会造状态的读取函数**。
    """
    whisper_service._tasks["c1"] = TaskState(course_id="c1", status="generating")
    assert whisper_service.active_task_count() == 1

    # 模拟列表端点对 10 行逐个查询（其中 9 行从未入队）
    for course_id in (f"row{i}" for i in range(10)):
        assert whisper_service.peek_status(course_id) is None

    assert whisper_service.active_task_count() == 1, "peek 不该凭空造出 pending 任务"
    assert not hasattr(whisper_service, "get_status"), (
        "get_status 有凭空造 TaskState 的副作用，已删除；"
        "需要状态一律用 peek_status() / task_exists() / task_is_active()"
    )


def test_enqueue_returns_queue_position(monkeypatch):
    """排队位次要真实：第 1 个入队 0，第 2 个 1（前端显示"排队中（第 N 位）"）。"""
    monkeypatch.setattr(whisper_service, "_start_worker", lambda: None)

    first = whisper_service.enqueue("c1", "/v/1.mp4")
    assert first["queue_position"] == 0

    second = whisper_service.enqueue("c2", "/v/2.mp4")
    assert second["queue_position"] == 1

    # 重复入队同一个 course_id：返回当前位次，不重复排队
    again = whisper_service.enqueue("c1", "/v/1.mp4")
    assert again["message"] == "已在队列中"
    assert again["queue_position"] == 0
    assert whisper_service._queue.count("c1") == 1


def test_enqueue_resets_slice_progress(monkeypatch):
    """重新入队必须清掉上一轮的切片进度，否则前端会看到残留的 18/45 与旧计时。"""
    monkeypatch.setattr(whisper_service, "_start_worker", lambda: None)

    st = TaskState(course_id="c1", status="error")
    st.slices_done = 18
    st.slices_total = 45
    st.phase = "merging"
    st.started_at = 1700000000.0
    st.error = "上一次炸了"
    whisper_service._tasks["c1"] = st

    whisper_service.enqueue("c1", "/v/1.mp4")

    assert st.slices_done == 0
    assert st.slices_total == 0
    assert st.phase is None
    assert st.started_at is None
    assert st.error is None
    assert st.status == "pending"
