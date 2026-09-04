"""P2 测试：60s 切片 + 逐片落盘 + 断点续跑 + 生成中取消 + 失败重试。

全部用 fake faster-whisper + monkeypatch，不真实加载模型、不真实跑 ffmpeg。
关键约定：
  - monkeypatch `faster_whisper` 模块让 FakeModel 从切片文件路径解析片序号 i；
  - monkeypatch `_probe_duration` 返回固定时长（决定切片数）；
  - monkeypatch `_slice_media` 仅建一个空切片文件（FakeModel 忽略内容，只取片序号）。
"""

import json
import re
import sys
import types

import pytest
from pathlib import Path
from types import SimpleNamespace

from app.services import whisper_service
from app.services.whisper_service import TaskState


@pytest.fixture(autouse=True)
def reset_whisper_state():
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()
    yield
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()


def _make_slice_aware_fake(segments_per_slice=2, recorded=None):
    """FakeModel：从切片文件路径 `slice<数字>` 解析片序号，返回该片相对时间戳（0..60）的 cues。"""
    fake = types.ModuleType("faster_whisper")

    class FakeInfo:
        duration = 60.0

    info = FakeInfo()

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            if recorded is not None:
                recorded.append(path)
            m = re.search(r"slice(\d+)", path)
            i = int(m.group(1)) if m else 0
            cues = [
                {"start": 1.0, "end": 5.0, "text": f"S{i}-A"},
                {"start": 6.0, "end": 10.0, "text": f"S{i}-B"},
            ][: max(0, segments_per_slice)]
            return iter([SimpleNamespace(start=c["start"], end=c["end"], text=c["text"]) for c in cues]), info

    fake.WhisperModel = FakeModel
    return fake


def _fake_slice_media(video_path, slice_index, slice_sec, ffmpeg):
    """只建一个空切片文件，让 FakeModel 能解析片序号；不真实跑 ffmpeg。"""
    base = Path(video_path).with_suffix("")
    p = base.with_name(f"{base.name}.whisper.slice{slice_index}.mp4")
    p.write_text("")
    return str(p)


def _patch_slice_env(monkeypatch, duration, segments_per_slice=2, recorded=None):
    monkeypatch.setattr(whisper_service, "_probe_duration", lambda vp: duration)
    monkeypatch.setattr(whisper_service, "_slice_media", _fake_slice_media)
    monkeypatch.setitem(sys.modules, "faster_whisper", _make_slice_aware_fake(segments_per_slice, recorded))


def _part_path(video, i):
    base = Path(video).with_suffix("")
    return base.with_name(f"{base.name}.whisper.slice{i}.part")


def test_slicing_shifts_timestamps(tmp_path, monkeypatch):
    """130s 视频 → 3 切片 → 合并后时间戳按 i*60 平移。"""
    _patch_slice_env(monkeypatch, duration=130.0)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    st = TaskState(course_id="c1")
    vtt_path = whisper_service._run_whisper("c1", str(video), st)

    content = Path(vtt_path).read_text(encoding="utf-8")
    # 片 2（i=2）首条相对 1.0s → 平移后 121.0s = 00:02:01.000
    assert "00:02:01.000 --> 00:02:05.000" in content
    assert "S2-A" in content
    assert "S0-A" in content and "00:00:01.000 --> 00:00:05.000" in content
    assert st.progress == 1.0
    # 合并成功应清掉 .part 状态文件
    assert not _part_path(video, 0).exists()
    assert not _part_path(video, 2).exists()


def test_resume_reuses_completed_parts(tmp_path, monkeypatch):
    """预置 slice0/1 的 .part，重跑只转写 slice2（断点续跑）。"""
    _patch_slice_env(monkeypatch, duration=130.0)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    # 预置已完成片（相对时间戳 cues）
    _part_path(video, 0).write_text(json.dumps([{"start": 1.0, "end": 5.0, "text": "S0-A"}]))
    _part_path(video, 1).write_text(json.dumps([{"start": 1.0, "end": 5.0, "text": "S1-A"}]))

    recorded = []
    _patch_slice_env(monkeypatch, duration=130.0, recorded=recorded)  # 重新 patch 以带上 recorded
    # 注意：上面两行重复 patch 只是保险；recorded 在第二次 patch 生效

    whisper_service._run_whisper("c1", str(video), TaskState(course_id="c1"))

    # 只转写了 slice2（slice0/1 从 .part 复用）
    transcoded = [p for p in recorded if "slice2" in p]
    assert len(transcoded) == 1
    assert not any("slice0" in p for p in recorded)
    content = (tmp_path / "video.whisper.vtt").read_text(encoding="utf-8")
    assert "S0-A" in content and "S1-A" in content and "S2-A" in content


def test_cancel_during_generation_keeps_completed_parts(tmp_path, monkeypatch):
    """生成中取消：切片间检测 _cancel_requested，抛出 _CancelledError，已完成片保留供续跑。"""
    course_id = "c_cancel"
    _patch_slice_env(monkeypatch, duration=130.0)

    # 让 slice0 转写完成后置位取消请求，下一轮循环顶部检测即触发取消
    orig = whisper_service._transcribe_slice

    def fake_ts(model, slice_path, initial_prompt, language, retries):
        m = re.search(r"slice(\d+)", slice_path)
        i = int(m.group(1)) if m else 0
        if i == 0:
            whisper_service._cancel_requested.add(course_id)
        return orig(model, slice_path, initial_prompt, language, retries)

    monkeypatch.setattr(whisper_service, "_transcribe_slice", fake_ts)

    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    st = TaskState(course_id=course_id)
    with pytest.raises(whisper_service._CancelledError):
        whisper_service._run_whisper(course_id, str(video), st)

    # slice0 的 .part 应保留（供续跑）；slice1/2 未开始，不存在
    assert _part_path(video, 0).exists()
    assert not _part_path(video, 1).exists()
    assert course_id in whisper_service._cancel_requested


def test_slice_transient_failure_retries(tmp_path, monkeypatch):
    """单切片转写前 2 次失败，第 3 次成功（SLICE_RETRIES=2 → 共 3 次尝试）。"""
    fake = types.ModuleType("faster_whisper")
    info = SimpleNamespace(duration=60.0)
    attempts = {}

    class FlakyModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kw):
            m = re.search(r"slice(\d+)", path)
            i = int(m.group(1)) if m else 0
            attempts[f"slice{i}"] = attempts.get(f"slice{i}", 0) + 1
            if attempts[f"slice{i}"] <= 2:
                raise RuntimeError(f"transient fail slice{i}")
            return iter([SimpleNamespace(start=1.0, end=5.0, text=f"ok{i}")]), info

    fake.WhisperModel = FlakyModel

    monkeypatch.setattr(whisper_service, "_probe_duration", lambda vp: 60.0)
    monkeypatch.setattr(whisper_service, "_slice_media", _fake_slice_media)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    vtt_path = whisper_service._run_whisper("c1", str(video), TaskState(course_id="c1"))

    # slice0 重试到第 3 次成功
    assert attempts["slice0"] == 3
    content = Path(vtt_path).read_text(encoding="utf-8")
    assert "ok0" in content


def test_whole_file_when_duration_unknown(tmp_path, monkeypatch):
    """探测不到时长 → 退化为整文件一次转写（单切片，无续跑粒度，不切媒体）。"""
    recorded = []
    _patch_slice_env(monkeypatch, duration=0.0, recorded=recorded)

    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    vtt_path = whisper_service._run_whisper("c1", str(video), TaskState(course_id="c1"))

    # 整文件转写：transcribe 只调一次，且路径不含 slice（即原始 video_path）
    assert len(recorded) == 1
    assert "slice" not in recorded[0]
    content = Path(vtt_path).read_text(encoding="utf-8")
    assert "S0-A" in content
    # 无切片，不应产生 .part
    assert not _part_path(video, 0).exists()
