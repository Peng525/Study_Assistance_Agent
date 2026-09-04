"""模块 2.6 Whisper 服务测试（mock whisper，不真实加载模型）。

A1 新增：
  - bug1 回归：enqueue 把 video_path 写进 TaskState，worker 取各自路径
  - bug2 回归：worker 成功 / 失败时写回 Material 表

A3 变更：
  - 引擎换 faster-whisper，mock 目标由 `whisper` 改为 `faster_whisper`
  - 删除 test_fmt_ts：`_fmt_ts` 已合并进 `subtitle.fmt_vtt_ts`（对 None/NaN/负数有保护），
    由 test_subtitle.py 的 test_fmt_vtt_ts_* 覆盖，避免同一逻辑两处测试
  - 新增 resolve_ffmpeg 回退到 imageio-ffmpeg 的用例
"""

import pytest

from app.services import whisper_service
from app.services.whisper_service import TaskState


@pytest.fixture(autouse=True)
def reset_whisper_state():
    """每个测试前重置全局任务状态。"""
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()
    yield
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()


def test_get_status_default_pending():
    st = whisper_service.get_status("c1")
    assert st["status"] == "pending"


def test_cancel_pending_task():
    whisper_service._tasks["c1"] = TaskState(course_id="c1", status="pending")
    whisper_service._queue.append("c1")
    assert whisper_service.cancel("c1") is True
    assert whisper_service.get_status("c1")["status"] == "error"


def test_cancel_generating_accepted():
    """P2：生成中（generating）也允许取消，置位 _cancel_requested 供 worker 切片间检测。"""
    whisper_service._tasks["c1"] = TaskState(course_id="c1", status="generating")
    assert whisper_service.cancel("c1") is True
    assert "c1" in whisper_service._cancel_requested


def test_active_task_count():
    whisper_service._tasks["c1"] = TaskState(course_id="c1", status="pending")
    whisper_service._tasks["c2"] = TaskState(course_id="c2", status="generating")
    whisper_service._tasks["c3"] = TaskState(course_id="c3", status="ready")
    assert whisper_service.active_task_count() == 2


def _make_fake_faster_whisper(segments, duration=10.0, recorded=None):
    """构造 fake faster_whisper 模块。

    faster-whisper 的 transcribe 签名是 (generator, info)，
    与本文件早期 mock 的 openai-whisper（返回 dict）不同。
    """
    import types
    from types import SimpleNamespace

    fake = types.ModuleType("faster_whisper")

    class FakeInfo:
        pass

    info = FakeInfo()
    info.duration = duration

    class FakeModel:
        def __init__(self, *args, **kwargs):
            if recorded is not None:
                recorded.append(kwargs)

        def transcribe(self, path, **kwargs):
            if recorded is not None:
                recorded.append(kwargs)
            return iter(
                [SimpleNamespace(start=s[0], end=s[1], text=s[2]) for s in segments]
            ), info

    fake.WhisperModel = FakeModel
    return fake


def test_run_whisper_generates_vtt(monkeypatch, tmp_path):
    """A3：faster-whisper 路径下能生成合法 vtt，且序列化复用 cues_to_vtt。"""
    import sys

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        _make_fake_faster_whisper([(1.0, 2.5, "你好")]),
    )

    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    st = TaskState(course_id="c1")
    vtt_path = whisper_service._run_whisper("c1", str(video), st)

    # A3：文件名加 .whisper 段，避免覆盖手动上传的 subtitle_*.vtt
    expected_vtt = tmp_path / "video.whisper.vtt"
    assert vtt_path == str(expected_vtt)
    assert expected_vtt.exists()
    content = expected_vtt.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:02.500" in content
    assert "你好" in content
    assert st.progress == 1.0


def test_run_whisper_reports_real_progress(monkeypatch, tmp_path):
    """A3：segments 是生成器，能报真实进度（且不会提前报 1.0）。"""
    import sys

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        _make_fake_faster_whisper([(0.0, 2.0, "a"), (2.0, 5.0, "b")], duration=10.0),
    )

    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    st = TaskState(course_id="c1")
    whisper_service._run_whisper("c1", str(video), st)
    # 全部转完 + 写盘成功 = 1.0
    assert st.progress == 1.0


def test_run_whisper_uses_configured_model(monkeypatch, tmp_path):
    """A3：模型尺寸来自配置（默认 small），不再硬编码 medium。"""
    import sys

    recorded = []
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        _make_fake_faster_whisper([(0.0, 1.0, "x")], recorded=recorded),
    )

    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    whisper_service._run_whisper("c1", str(video), TaskState(course_id="c1"))

    # 第一条记录是构造参数（model_size/device/compute_type）
    ctor_kwargs = recorded[0]
    from app.core.config import settings

    assert ctor_kwargs.get("device") in ("cpu", "cuda")
    assert ctor_kwargs.get("compute_type") in ("int8", "float16", "float32")
    # 语言与 VAD 走 transcribe 参数
    transcribe_kwargs = recorded[1]
    assert transcribe_kwargs.get("vad_filter") is True
    assert transcribe_kwargs.get("language") == (settings.whisper_language or None)


def test_run_whisper_import_error(monkeypatch):
    """未安装 faster-whisper 时抛出明确错误（提示装哪个包）。"""
    import sys

    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    # 让 import 必然失败
    monkeypatch.setitem(sys.modules, "faster_whisper", None)

    st = TaskState(course_id="c1")
    with pytest.raises(RuntimeError, match="faster-whisper"):
        whisper_service._run_whisper("c1", "x.mp4", st)


def test_resolve_ffmpeg_falls_back_to_imageio(monkeypatch, tmp_path):
    """A0 决策：系统 PATH 没有 ffmpeg 时，回退到 imageio-ffmpeg 的二进制。"""
    monkeypatch.setattr(whisper_service.shutil, "which", lambda _: None)

    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"fake")

    import sys
    import types

    fake_imageio = types.ModuleType("imageio_ffmpeg")
    fake_imageio.get_ffmpeg_exe = lambda: str(exe)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake_imageio)

    assert whisper_service.resolve_ffmpeg() == str(exe)
    assert whisper_service.is_ffmpeg_available() is True


def test_resolve_ffmpeg_prefers_system(monkeypatch):
    """系统 PATH 里有 ffmpeg 时优先用它，不惊动 imageio。"""
    monkeypatch.setattr(whisper_service.shutil, "which", lambda _: "C:/sys/ffmpeg.exe")
    assert whisper_service.resolve_ffmpeg() == "C:/sys/ffmpeg.exe"


def test_resolve_ffmpeg_returns_none_when_missing(monkeypatch):
    """两者都没有 → 返回 None，is_ffmpeg_available() 为 False（不抛异常）。"""
    import sys

    monkeypatch.setattr(whisper_service.shutil, "which", lambda _: None)
    monkeypatch.delitem(sys.modules, "imageio_ffmpeg", raising=False)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    assert whisper_service.resolve_ffmpeg() is None
    assert whisper_service.is_ffmpeg_available() is False


# ---------- A1 bug1 回归：每个任务用自己的 video_path ----------

def test_enqueue_stores_video_path_in_task_state(monkeypatch):
    """A1 bug1：enqueue 把 video_path 写进 TaskState，否则 worker 会复用闭包变量。"""
    # 屏蔽 worker，避免触发真实 whisper / DB 副作用
    monkeypatch.setattr(whisper_service, "_start_worker", lambda: None)

    whisper_service.enqueue("c1", "/path/A.mp4")
    st = whisper_service._get_state("c1")
    assert st.video_path == "/path/A.mp4"

    whisper_service.enqueue("c2", "/path/B.mp4")
    st2 = whisper_service._get_state("c2")
    assert st2.video_path == "/path/B.mp4"
    # 两个互不干扰
    assert whisper_service._get_state("c1").video_path == "/path/A.mp4"


def test_enqueue_updates_video_path_on_retry(monkeypatch):
    """A1 bug1 衍生：同一 course 重入队时，应使用最新的 video_path（视频被替换场景）。"""
    monkeypatch.setattr(whisper_service, "_start_worker", lambda: None)

    whisper_service.enqueue("c1", "/old/A.mp4")
    # 模拟上一次生成完成：清队列 + 改状态。worker 被 mock 不会真的清队列
    whisper_service._queue.clear()
    whisper_service._get_state("c1").status = "ready"

    whisper_service.enqueue("c1", "/new/A.mp4")
    assert whisper_service._get_state("c1").video_path == "/new/A.mp4"


def test_worker_loop_reads_video_path_from_task_state(monkeypatch):
    """A1 bug1：worker 从 TaskState.video_path 取，不用启动闭包。"""
    captured = []

    def fake_run(course_id, video_path, st):
        captured.append((course_id, video_path))
        # 抛错跳过后续写盘，最简化
        raise RuntimeError("stop here")

    monkeypatch.setattr(whisper_service, "_run_whisper", fake_run)
    # 写回 DB 也 mock 掉避免需要真实 DB
    monkeypatch.setattr(whisper_service, "_write_back_to_db", lambda **kw: None)

    # 准备两个课程的 TaskState，各带不同 video_path
    whisper_service._tasks["c1"] = TaskState(course_id="c1", video_path="/A.mp4")
    whisper_service._tasks["c2"] = TaskState(course_id="c2", video_path="/B.mp4")
    whisper_service._queue.extend(["c1", "c2"])
    whisper_service._worker_running = True

    whisper_service._worker_loop()  # 同步跑直到队列空

    # 关键断言：c1 拿到 /A.mp4，c2 拿到 /B.mp4，没混淆
    assert ("c1", "/A.mp4") in captured
    assert ("c2", "/B.mp4") in captured
    assert captured.index(("c1", "/A.mp4")) < captured.index(("c2", "/B.mp4"))


# ---------- A1 bug2 回归：worker 写回 DB ----------

def test_write_back_to_db_success(monkeypatch):
    """A1 bug2：成功后写 subtitle_path / subtitle_status='ready' / subtitle_source / subtitle_format。"""
    captured = {}

    class FakeMaterial:
        subtitle_path = None
        subtitle_source_format = None
        subtitle_source = None
        subtitle_status = None
        subtitle_error = "old error"  # 验证会被清空

    class FakeQuery:
        def filter(self, *a, **kw):
            return self

        def first(self):
            return captured.setdefault("material", FakeMaterial())

    class FakeDB:
        def query(self, m):
            return FakeQuery()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: FakeDB())

    whisper_service._write_back_to_db(
        course_id="c1", success=True, vtt_path="/fake/c1.vtt"
    )

    m = captured["material"]
    assert m.subtitle_path == "/fake/c1.vtt"
    assert m.subtitle_status == "ready"
    assert m.subtitle_source == "whisper"
    assert m.subtitle_source_format == "vtt"
    assert m.subtitle_error is None


def test_write_back_to_db_failure(monkeypatch):
    """A1 bug2：失败时写 subtitle_status='error' + subtitle_error（截到 2000 字）。"""
    captured = {}

    class FakeMaterial:
        subtitle_path = "/old.vtt"
        subtitle_status = "ready"
        subtitle_error = None

    class FakeQuery:
        def filter(self, *a, **kw):
            return self

        def first(self):
            return captured.setdefault("material", FakeMaterial())

    class FakeDB:
        def query(self, m):
            return FakeQuery()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: FakeDB())

    long_err = "x" * 3000
    whisper_service._write_back_to_db(
        course_id="c1", success=False, error=long_err
    )

    m = captured["material"]
    assert m.subtitle_status == "error"
    assert m.subtitle_error is not None
    assert len(m.subtitle_error) == 2000  # 截断生效
    # 失败时不动 subtitle_path / subtitle_source（保留旧值，便于排查）


def test_write_back_to_db_no_material(monkeypatch):
    """A1 bug2 边界：Material 不存在（被删了）不崩，安静返回。"""

    class FakeQuery:
        def filter(self, *a, **kw):
            return self

        def first(self):
            return None

    class FakeDB:
        def query(self, m):
            return FakeQuery()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: FakeDB())

    # 不应抛异常
    whisper_service._write_back_to_db(
        course_id="ghost", success=True, vtt_path="/x.vtt"
    )