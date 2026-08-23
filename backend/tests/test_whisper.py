"""模块 2.6 Whisper 服务测试（mock whisper，不真实加载模型）。"""

import pytest

from app.services import whisper_service
from app.services.whisper_service import TaskState, _fmt_ts


@pytest.fixture(autouse=True)
def reset_whisper_state():
    """每个测试前重置全局任务状态。"""
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    yield
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False


def test_fmt_ts():
    assert _fmt_ts(0) == "00:00:00.000"
    assert _fmt_ts(61.5) == "00:01:01.500"
    assert _fmt_ts(3661.25) == "01:01:01.250"


def test_get_status_default_pending():
    st = whisper_service.get_status("c1")
    assert st["status"] == "pending"


def test_cancel_pending_task():
    whisper_service._tasks["c1"] = TaskState(course_id="c1", status="pending")
    whisper_service._queue.append("c1")
    assert whisper_service.cancel("c1") is True
    assert whisper_service.get_status("c1")["status"] == "error"


def test_cancel_non_pending_rejected():
    whisper_service._tasks["c1"] = TaskState(course_id="c1", status="generating")
    assert whisper_service.cancel("c1") is False


def test_active_task_count():
    whisper_service._tasks["c1"] = TaskState(course_id="c1", status="pending")
    whisper_service._tasks["c2"] = TaskState(course_id="c2", status="generating")
    whisper_service._tasks["c3"] = TaskState(course_id="c3", status="ready")
    assert whisper_service.active_task_count() == 2


def test_run_whisper_generates_vtt(monkeypatch, tmp_path):
    """mock whisper 模块，验证 vtt 文件生成。"""
    import types

    fake_whisper = types.ModuleType("whisper")

    class FakeModel:
        def transcribe(self, video_path, verbose=False):
            return {"segments": [{"start": 1.0, "end": 2.5, "text": "你好"}]}

    fake_whisper.load_model = lambda name: FakeModel()

    # 注入到 sys.modules 让 import whisper 命中
    import sys

    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)

    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    st = TaskState(course_id="c1")
    whisper_service._run_whisper(str(video), st)

    vtt_path = tmp_path / "video.vtt"
    assert vtt_path.exists()
    content = vtt_path.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:02.500" in content
    assert "你好" in content


def test_run_whisper_import_error():
    """未安装 whisper 时抛出明确错误。"""
    import sys

    # 确保 whisper 不在 sys.modules
    saved = sys.modules.pop("whisper", None)
    try:
        st = TaskState(course_id="c1")
        with pytest.raises(RuntimeError, match="openai-whisper"):
            whisper_service._run_whisper("x.mp4", st)
    finally:
        if saved is not None:
            sys.modules["whisper"] = saved
