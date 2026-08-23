"""Whisper 自动字幕生成服务（异步任务：线程池 + 状态轮询）。

- 队列串行：同一时间只运行一个生成任务，避免内存爆炸
- 状态流转：pending → generating → ready / error
- ffmpeg 依赖检测
"""

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# 任务状态
PENDING = "pending"
GENERATING = "generating"
READY = "ready"
ERROR = "error"

# 全局任务表：course_id -> TaskState
_tasks: dict[str, "TaskState"] = {}
_lock = threading.Lock()
_queue: list[str] = []
_worker_running = False


@dataclass
class TaskState:
    course_id: str
    status: str = PENDING
    progress: float = 0.0
    error: str | None = None
    queue_position: int = 0


def _get_state(course_id: str) -> TaskState:
    with _lock:
        if course_id not in _tasks:
            _tasks[course_id] = TaskState(course_id=course_id)
        return _tasks[course_id]


def get_status(course_id: str) -> dict:
    st = _get_state(course_id)
    with _lock:
        return {
            "status": st.status,
            "progress": st.progress,
            "error": st.error,
            "queue_position": st.queue_position,
        }


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def cancel(course_id: str) -> bool:
    """取消排队中的任务（仅 pending 状态可取消）。"""
    with _lock:
        st = _tasks.get(course_id)
        if st is None or st.status != PENDING:
            return False
        if course_id in _queue:
            _queue.remove(course_id)
        st.status = ERROR
        st.error = "已取消"
        st.queue_position = 0
    return True


def active_task_count() -> int:
    with _lock:
        return sum(1 for st in _tasks.values() if st.status in (PENDING, GENERATING))


def enqueue(course_id: str, video_path: str) -> dict:
    """将字幕生成任务入队（串行）。"""
    st = _get_state(course_id)
    with _lock:
        if st.status == GENERATING:
            return {"status": GENERATING, "message": "字幕生成中"}
        if course_id in _queue:
            return {"status": PENDING, "message": "已在队列中"}
        st.status = PENDING
        st.progress = 0.0
        st.error = None
        _queue.append(course_id)
    global _worker_running
    if not _worker_running:
        _start_worker(video_path)
    return {"status": PENDING, "message": "已加入字幕生成队列"}


def _start_worker(video_path: str):
    global _worker_running
    _worker_running = True
    t = threading.Thread(target=_worker_loop, args=(video_path,), daemon=True)
    t.start()


def _worker_loop(video_path: str):
    """后台串行处理队列。"""
    global _worker_running
    while True:
        with _lock:
            if not _queue:
                _worker_running = False
                return
            course_id = _queue[0]
        st = _get_state(course_id)
        with _lock:
            st.status = GENERATING
            st.progress = 0.0
            # 更新队列位置
            for i, cid in enumerate(_queue):
                _get_state(cid).queue_position = i
        try:
            _run_whisper(video_path, st)
            with _lock:
                st.status = READY
                st.progress = 1.0
        except Exception as e:  # noqa: BLE001
            with _lock:
                st.status = ERROR
                st.error = str(e)
        finally:
            with _lock:
                _queue.pop(0)
                # 更新队列位置
                for i, cid in enumerate(_queue):
                    _get_state(cid).queue_position = i


def _run_whisper(video_path: str, st: TaskState):
    """实际调用 Whisper 转录（延迟导入，避免未安装影响其他模块）。"""
    try:
        import whisper
    except ImportError as e:
        raise RuntimeError("未安装 openai-whisper，请先 pip install openai-whisper") from e

    model = whisper.load_model("medium")
    result = model.transcribe(video_path, verbose=False)
    segments = result.get("segments", [])

    # 生成 VTT 文本
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _fmt_ts(seg["start"])
        end = _fmt_ts(seg["end"])
        text = seg.get("text", "").strip()
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    vtt_content = "\n".join(lines)

    # 写入视频同目录下的 subtitle.vtt
    video = Path(video_path)
    vtt_path = video.with_suffix(".vtt")
    vtt_path.write_text(vtt_content, encoding="utf-8")
    st.progress = 1.0


def _fmt_ts(seconds: float) -> str:
    ms = int(seconds * 1000)
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    milli = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{milli:03d}"
