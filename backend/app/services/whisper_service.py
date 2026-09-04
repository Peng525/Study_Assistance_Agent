"""Whisper 自动字幕生成服务（异步任务：线程池 + 状态轮询）。

- 队列串行：同一时间只运行一个生成任务，避免内存爆炸
- 状态流转：pending → generating → ready / error
- ffmpeg 依赖检测

A1 修复（2026-09-04）：
  - bug1：worker 不再用启动时的闭包 video_path，改从 TaskState.video_path 取，
          每个任务用各自的视频路径。原 bug 在多任务并发时，所有任务会用首个任务的 video_path。
  - bug2：worker 用独立 SessionLocal 把结果写回 Material 表（subtitle_path / subtitle_status /
          subtitle_source_format / subtitle_source），失败时写 subtitle_status='error' +
          subtitle_error。原 bug 让 DB 永远停在 generating。

A3 变更（2026-09-04）：
  - 引擎由 openai-whisper 换成 **faster-whisper**（无 torch 依赖、内置 VAD、
    segments 是生成器所以能给真实进度）。模型尺寸 / 设备 / 精度 / 语言全部走
    core/config.py 的配置项，不再硬编码 medium。
  - 序列化统一复用 `app.services.subtitle.cues_to_vtt`（A2），
    删除本模块重复的 `_fmt_ts`（原实现对 None / 负数 / NaN 无保护）。
"""

import json
import math
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import Material
from app.services.subtitle import cues_to_vtt

# 任务状态
PENDING = "pending"
GENERATING = "generating"
READY = "ready"
ERROR = "error"

# 全局任务表：course_id -> TaskState
_tasks: dict[str, "TaskState"] = {}
_lock = threading.RLock()    # A1：用 RLock 而非 Lock——_get_state 在 _worker_loop 的
                             # `with _lock:` 块内被调用（更新 queue_position），
                             # 普通 Lock 会自死锁。原代码 bug，仅当 worker 真的跑起来才暴露。
_queue: list[str] = []          # 队列只存 course_id；video_path 从 TaskState 取（A1 bug1 修复）
_worker_running = False

# 生成中途取消请求集合（cancel() 在 generating 时也置位，worker 切片间检测后优雅停止）
_cancel_requested: set[str] = set()
# P2 切片参数（接手文档 5.1 决策）：每片 60 秒；单切片转写失败重试 2 次
SLICE_SEC = 60
SLICE_RETRIES = 2


@dataclass
class TaskState:
    course_id: str
    video_path: str | None = None  # A1：每次 enqueue 时更新，保证最新入队的视频路径生效
    status: str = PENDING
    progress: float = 0.0
    error: str | None = None
    queue_position: int = 0


class _CancelledError(Exception):
    """生成中途被取消（cancel() 在 generating 时置位 _cancel_requested 触发）。"""


def _is_cancel_requested(course_id: str) -> bool:
    return course_id in _cancel_requested


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


def resolve_ffmpeg() -> str | None:
    """返回可用的 ffmpeg 可执行文件路径，没有则返回 None。

    两级查找（A0 决策）：
      1. 系统 PATH 里的 ffmpeg（用户自己装过就用他的）
      2. imageio-ffmpeg 随包自带的二进制（pip 装进 venv，不写 PATH、不需要管理员权限，
         下一个人 clone 后 pip install 即可用）

    注意：faster-whisper 转写本身走 PyAV 解码，**不依赖 ffmpeg**；
    ffmpeg 目前只在切片续跑（Deferred 的 B 阶段）才真正需要。
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        exe = get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:  # noqa: BLE001 — 没装 imageio-ffmpeg 就当没有，不该让状态查询崩
        pass
    return None


def is_ffmpeg_available() -> bool:
    return resolve_ffmpeg() is not None


def cancel(course_id: str) -> bool:
    """取消生成任务。排队中（pending）或生成中（generating）均可取消。

    - pending：立即移出队列并置 error。
    - generating：置位 `_cancel_requested`，worker 在切片间检测到后优雅停止
      （已完成片的 .part 保留，供后续重新生成时断点续跑）。
    """
    with _lock:
        st = _tasks.get(course_id)
        if st is None:
            return False
        if st.status == PENDING:
            if course_id in _queue:
                _queue.remove(course_id)
            st.status = ERROR
            st.error = "已取消"
            st.queue_position = 0
            _cancel_requested.discard(course_id)
            return True
        if st.status == GENERATING:
            _cancel_requested.add(course_id)
            return True
    return False


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
        st.video_path = video_path       # A1：每次入队都用最新传入的路径
        _cancel_requested.discard(course_id)  # 新任务清掉旧的取消标记
        st.status = PENDING
        st.progress = 0.0
        st.error = None
        _queue.append(course_id)
    global _worker_running
    if not _worker_running:
        _start_worker()
    return {"status": PENDING, "message": "已加入字幕生成队列"}


def _start_worker():
    """启动后台 worker（A1：不再需要传 video_path 闭包，bug1 修复）。"""
    global _worker_running
    _worker_running = True
    t = threading.Thread(target=_worker_loop, daemon=True)
    t.start()


def _worker_loop():
    """后台串行处理队列。每个任务从自己的 TaskState.video_path 取视频路径。"""
    global _worker_running
    while True:
        with _lock:
            if not _queue:
                _worker_running = False
                return
            course_id = _queue[0]
        st = _get_state(course_id)
        video_path = st.video_path        # A1 bug1：从 TaskState 取（之前用启动时的闭包变量）
        if not video_path:
            with _lock:
                st.status = ERROR
                st.error = "缺少 video_path"
            _write_back_to_db(course_id=course_id, success=False, error="缺少 video_path")
            with _lock:
                _queue.pop(0)
                for i, cid in enumerate(_queue):
                    _get_state(cid).queue_position = i
            continue
        with _lock:
            st.status = GENERATING
            st.progress = 0.0
            for i, cid in enumerate(_queue):
                _get_state(cid).queue_position = i
        try:
            vtt_path = _run_whisper(course_id, video_path, st)
            with _lock:
                st.status = READY
                st.progress = 1.0
            _write_back_to_db(course_id=course_id, success=True, vtt_path=vtt_path)
        except _CancelledError:
            # 生成中途取消：保留已完成片的 .part（供续跑），状态置 error
            with _lock:
                st.status = ERROR
                st.error = "已取消"
            try:
                _write_back_to_db(course_id=course_id, success=False, error="已取消")
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            with _lock:
                st.status = ERROR
                st.error = str(e)
            try:
                _write_back_to_db(course_id=course_id, success=False, error=str(e))
            except Exception:  # noqa: BLE001
                # DB 也挂了不二次崩；st.error 已记录，不会再让用户以为卡在 generating
                pass
        finally:
            with _lock:
                _queue.pop(0)
                for i, cid in enumerate(_queue):
                    _get_state(cid).queue_position = i


def _resolve_device(requested: str) -> str:
    """'auto' → 有 CUDA 就用 cuda，否则 cpu。

    用 ctranslate2 探测而不是 torch：faster-whisper 不走 torch，
    为一趟探测去 import torch（2GB+）不划算。
    """
    if requested != "auto":
        return requested
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:  # noqa: BLE001 — 探测失败一律退回 CPU，不该让生成任务起不来
        pass
    return "cpu"


def _resolve_compute_type(device: str, requested: str) -> str:
    """'default' → GPU 用 float16，CPU 用 int8（int8 在 CPU 上快 2~3 倍且省内存）。"""
    if requested != "default":
        return requested
    return "float16" if device == "cuda" else "int8"


def _probe_duration(video_path: str) -> float:
    """用 PyAV 探测总时长（秒）；拿不到返回 0（上层退化为整文件一次转写，无续跑粒度）。"""
    try:
        import av
    except Exception:  # noqa: BLE001 — PyAV 不在就退化，不该让生成任务起不来
        return 0.0
    try:
        with av.open(video_path) as container:
            d = container.duration
            if not d:
                return 0.0
            return float(d) / 1_000_000.0
    except Exception:  # noqa: BLE001
        return 0.0


def _slice_media(video_path: str, slice_index: int, slice_sec: int, ffmpeg: str) -> str:
    """ffmpeg 流拷贝切出第 slice_index 片（60s），返回临时切片媒体路径。

    流拷贝（-c copy）不重新编码，秒级完成。文件命名 `<base>.whisper.slice{i}.mp4`
    与本片落盘的 .part 同前缀，便于统一管理。切失败抛异常，由切片重试逻辑接管。
    """
    base = Path(video_path).with_suffix("")
    slice_path = base.with_name(f"{base.name}.whisper.slice{slice_index}.mp4")
    cmd = [
        ffmpeg, "-y", "-ss", str(slice_index * slice_sec), "-t", str(slice_sec),
        "-i", video_path, "-c", "copy", str(slice_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not slice_path.exists():
        raise RuntimeError(f"ffmpeg 切片失败: {proc.stderr[-500:]}")
    return str(slice_path)


def _tail_text(cues: list[dict], max_chars: int = 200) -> str:
    """取片末约 max_chars 字作为下一片 initial_prompt，承接上下文（成本≈0，不做 overlap 转写）。"""
    tail = " ".join((c.get("text") or "") for c in cues[-3:])
    return tail[-max_chars:]


def _write_atomic(path: Path, text: str) -> None:
    """原子写盘：先写 .tmp 再 replace，防止写一半断电留下坏文件（接手文档 5.1）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_part_atomic(path: Path, cues: list[dict]) -> None:
    _write_atomic(path, json.dumps(cues, ensure_ascii=False))


def _read_part(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _transcribe_slice(model, slice_path: str, initial_prompt: str, language: str | None, retries: int) -> list[dict]:
    """转写单个切片文件（相对时间戳 0..slice_sec）。失败按 retries 重试。"""
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            segs, _info = model.transcribe(
                slice_path,
                language=language or None,
                vad_filter=True,
                beam_size=5,
                initial_prompt=initial_prompt or None,
            )
            return [{"start": s.start, "end": s.end, "text": (s.text or "").strip()} for s in segs]
        except Exception as e:  # noqa: BLE001
            last = e
    raise last  # type: ignore[misc]


def _run_whisper(course_id: str, video_path: str, st: TaskState) -> str:
    """调用 faster-whisper 转录，返回生成的 vtt 文件路径。失败时抛异常。

    P2（续做）：60s 切片 + 逐片落盘 + 断点续跑 + 生成中取消 + 失败重试。
      - ffmpeg 流拷贝切出每片独立媒体（不重新编码），逐片转写；
      - 每片相对时间戳 0..60，落盘时平移 start/end += i*60（接手文档 5.1：时间戳平移）；
      - 每片写 `<base>.whisper.slice{i}.part`（JSON cues）——**文件系统即状态**，
        关机/崩溃重启后数已存在的 .part 即可续跑，不写 progress.json；
      - 切片间检测 `_cancel_requested` 实现生成中取消（已完成片保留供续跑）；
      - 单切片转写失败重试 SLICE_RETRIES 次。
    短于 60s 或探测不到时长 → 退化为整文件一次转写（单切片，无续跑粒度）。

    写盘文件名用 `<视频主名>.whisper.vtt`，避开手动上传字幕的 `subtitle_<uuid>.vtt` 命名。
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError("未安装 faster-whisper，请先 pip install faster-whisper") from e

    device = _resolve_device(settings.whisper_device)
    compute_type = _resolve_compute_type(device, settings.whisper_compute_type)
    model = WhisperModel(
        settings.whisper_model_size,
        device=device,
        compute_type=compute_type,
    )

    ffmpeg = resolve_ffmpeg()
    duration = _probe_duration(video_path)
    slice_sec = SLICE_SEC
    n_slices = max(1, math.ceil(duration / slice_sec)) if duration > 0 else 1
    base = Path(video_path).with_suffix("")
    part_paths = [base.with_name(f"{base.name}.whisper.slice{i}.part") for i in range(n_slices)]

    all_cues: list[dict] = []
    prev_tail = ""
    done = 0
    for i in range(n_slices):
        if _is_cancel_requested(course_id):
            raise _CancelledError(course_id)
        if part_paths[i].exists():
            slice_cues = _read_part(part_paths[i])            # 已完成片：直接复用（断点续跑）
        else:
            slice_path = _slice_media(video_path, i, slice_sec, ffmpeg) if (ffmpeg and n_slices > 1) else video_path
            try:
                slice_cues = _transcribe_slice(model, slice_path, prev_tail, settings.whisper_language, SLICE_RETRIES)
            finally:
                if slice_path != video_path:
                    Path(slice_path).unlink(missing_ok=True)   # 删临时切片媒体
            _write_part_atomic(part_paths[i], slice_cues)      # 落盘（续跑状态）
        shifted = [
            {"start": c["start"] + i * slice_sec, "end": c["end"] + i * slice_sec, "text": c["text"]}
            for c in slice_cues
        ]
        all_cues.extend(shifted)
        prev_tail = _tail_text(slice_cues)
        done += 1
        st.progress = done / n_slices
    vtt_content = cues_to_vtt(all_cues)
    vtt_path = base.with_name(f"{base.name}.whisper.vtt")
    _write_atomic(vtt_path, vtt_content)
    st.progress = 1.0
    for p in part_paths:
        p.unlink(missing_ok=True)   # 合并成功才清状态文件
    return str(vtt_path)


def _write_back_to_db(
    course_id: str,
    success: bool,
    vtt_path: str | None = None,
    error: str | None = None,
) -> None:
    """A1 bug2 修复：worker 把结果写回 Material 表。

    用独立 SessionLocal（不复用任何请求的 db session），写完即关。
    成功：subtitle_path / subtitle_status='ready' / subtitle_source='whisper' /
          subtitle_source_format='vtt' / subtitle_error=None
    失败：subtitle_status='error' / subtitle_error=msg（截到 2000 字防 SQLite 字段超限）

    测试入口：SessionLocal 与 Material 已提到模块顶层，测试用
    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: FakeDB()) 替换。
    """
    db = SessionLocal()
    try:
        material = db.query(Material).filter(Material.course_id == course_id).first()
        if material is None:
            # 任务还在跑、Material 被管理员删了——保守放弃写回，不崩
            return
        if success and vtt_path:
            material.subtitle_path = vtt_path
            material.subtitle_source_format = "vtt"
            material.subtitle_source = "whisper"
            material.subtitle_status = "ready"
            material.subtitle_error = None
        else:
            material.subtitle_status = "error"
            material.subtitle_error = (error or "未知错误")[:2000]
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
