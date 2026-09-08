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

import glob
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
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

# ---- 语言与课程术语提示词（small 模型的中英混合识别优化）----
# 转写语言。**不再允许"不传 language 让模型自动检测"** —— 中英混合的技术课程里
# 自动检测会把大量中文段落判成 en / ja，产出整段英文或乱字。留空配置时也回落到 zh。
DEFAULT_LANGUAGE = "zh"
# 单片 initial_prompt 的 token 预算（用户拍板：≤ 210~215）。
# 硬上限是 faster-whisper `get_prompt()` 的 223（`previous_tokens[-(max_length//2-1):]`），
# 取 215 是为了给估算误差留 8 个 token 的余量。
TOKEN_BUDGET = 215
MAX_SLICE_TAIL_CHARS = 60        # 尾部上下文的**字符**硬上限（token 预算再细裁）
MAX_INITIAL_PROMPT_CHARS = 260   # 兜底硬上限（术语表被换成超长词表时的护栏）
MAX_PROMPT_TERMS = 30
MAX_COURSE_NAME_CHARS = 60

# ---- 课程术语表（**课程专属，不是全局默认**）----
#
# ⚠️ 这是**当前 Demo 课程（Java / SSM 系列）的词表**，未登记的课程**不会**自动套用
# （见 `course_terms_for`）。不相关的课程被塞进 SSM 术语只会污染识别。
#
# ⚠️ **顺序 = 优先级**：课程核心词在前，扩展词在后，品牌名垫底。
# 超 token 预算时从**表尾**（低优先级）开始裁 —— 所以"尚硅谷"这种
# 认不出来也无所谓的品牌名放最后，先被牺牲。
#
# ⚠️ 只允许**标准拼写**。识别错误的结果（Sprin / Mapitis / 扎瓦 / 上规谷）绝不能进词表 ——
# 那等于教模型把错字当正字，比不放还糟。
#
# 后续替换为"从 PPT / 课程大纲自动抽取"时，只换 `COURSE_TERMS` 的内容或新增 key，
# `build_whisper_initial_prompt` 的调用点不变。
_JAVA_SSM_TERMS: tuple[str, ...] = (
    # —— 高优先级：课程核心术语 ——
    "Java",
    "SSM",
    "Spring",
    "Spring MVC",
    "Spring Boot",
    "Spring IoC",
    "Spring AOP",
    "MyBatis",
    "MVC",
    "IoC",
    "AOP",
    "Maven",
    # —— 扩展：同系列课程高频词（超预算时从这里开始裁）——
    "JavaWeb",
    "JDK",
    "Spring Cloud",
    "MyBatis-Plus",
    "Servlet",
    "JSP",
    "Tomcat",
    "MySQL",
    "Redis",
    "IDEA",
    "尚硅谷",   # 品牌名，识别不出也不影响理解 → 优先级最低
)
COURSE_TERMS: dict[str, tuple[str, ...]] = {
    "001.AI版SSM教程简介": _JAVA_SSM_TERMS,
}
# 同系列课程的低成本判定（Demo 阶段）：课程名里含这些关键词就复用 Java/SSM 词表。
# 将来由课程元数据（专栏 / 课程类型）驱动，届时删掉这个常量即可。
_COURSE_TERM_KEYWORDS = ("ssm", "java", "spring")


@dataclass
class TaskState:
    course_id: str
    video_path: str | None = None  # A1：每次 enqueue 时更新，保证最新入队的视频路径生效
    status: str = PENDING
    progress: float = 0.0
    # v8 细粒度进度（PRD §5.5A.4）：切片计数 + 阶段 + 开跑时间。
    # 前端据此显示 "生成中 43% / ███████░░░ / 18 / 45"；
    # 拿不到 slices_total（如时长探测失败）时降级为 "⏳ 正在生成字幕… 已运行 01:24"。
    slices_done: int = 0
    slices_total: int = 0
    phase: str | None = None          # 'transcribing' | 'merging' | None
    started_at: float | None = None   # Unix 时间戳
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


def peek_status(course_id: str) -> dict | None:
    """**唯一的**无副作用读取口：course_id 从未入队时返回 None，**不创建** TaskState。

    本模块曾同时存在 `get_status()`（内部走 `_get_state()`，会为任意 course_id 凭空造一个
    PENDING TaskState）。靠"文档里写清楚列表场景要用 peek"来约束是守不住的——
    新写的函数照样顺手用错。因此 `get_status()` 已被**删除**，只留这一个入口：
    **不存在"会造状态"的读取函数，也就不可能误用**。

    灌水的后果：对列表里 N 行逐行调用会让 `active_task_count()` 虚高，
    `/whisper/model-status` 的 active_tasks 从此不准，且"可取消的行"会被误判为可取消。
    """
    with _lock:
        st = _tasks.get(course_id)
        if st is None:
            return None
        return _snapshot(st)


def task_exists(course_id: str) -> bool:
    """该 course_id 是否存在**真实**的 runtime 任务（无副作用）。

    与 `peek_status() is not None` 等价，但语义更直白、且明确不含"顺手造一个"的副作用。
    用于「可取消的行」判定：DB 写着 `generating` 而这里是 False → orphan（PRD §5.5A.3）。
    """
    with _lock:
        return course_id in _tasks


def task_is_active(course_id: str) -> bool:
    """runtime 任务是否处于"还没结束"的状态（PENDING 或 GENERATING）。"""
    with _lock:
        st = _tasks.get(course_id)
        return st is not None and st.status in (PENDING, GENERATING)


def _snapshot(st: "TaskState") -> dict:
    """TaskState → dict。调用方需持有 _lock（或确认无并发写）。"""
    return {
        "status": st.status,
        "progress": st.progress,
        "slices_done": st.slices_done,
        "slices_total": st.slices_total,
        "phase": st.phase,
        "started_at": st.started_at,
        "error": st.error,
        "queue_position": st.queue_position,
    }


def zero_snapshot() -> dict:
    """任务不存在时的零值快照，字段与 `_snapshot()` 完全同构。

    给那些"拿不到状态也必须返回一个完整结构"的端点用（如 `subtitle-status` 单查）。
    ⚠️ 不要用 `get_status()` 兜底 —— 它会为陌生 course_id 凭空造一个 PENDING TaskState，
    把 `active_task_count()` 灌水（v9 修复：单查端点原先就是这么漏的）。
    该函数已删除，正确写法是 `peek_status(cid) or zero_snapshot()`。
    """
    return {
        "status": PENDING,
        "progress": 0.0,
        "slices_done": 0,
        "slices_total": 0,
        "phase": None,
        "started_at": None,
        "error": None,
        "queue_position": 0,
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


def cancel(course_id: str) -> str | None:
    """取消生成任务，返回取消发生的时机；**无法取消时返回 None**。

    返回值：
      - `"queued"` —— 排队中取消。任务在**同一个持锁段内**被完整收尾：
        出队 + 清 runtime + 清取消标记 + 重排位次。
        **DB 必须由调用方写回 `pending`**：worker 只处理从队列取到的 course_id，
        已出队的任务永远不会再被它碰（详见 `_dequeue` 注释）。调用方不写回，
        DB 就会永久停在 `generating`，前端对着一个不推进的进度条空轮询到进程重启。
      - `"generating"` —— 生成中取消。已置位 `_cancel_requested`，
        由 worker 在 7 个检测点响应，**DB 由 worker 自己写回**（`pending` + `error=None`）。
        这里不预写 DB：预写会让"生成中"的行先闪成"待生成"再被 worker 的收尾覆盖，
        中间那一帧是自相矛盾的。
      - `None` —— 没有真实任务可取消（从未入队 / 已完成 / 已失败）。
        调用方**必须据此报错**，禁止返回假成功：
        管理员点了取消、UI 说"已取消"、任务其实还在跑，是最难排查的一类事故。

    ⚠️ 排队中分支必须 `_tasks.pop`：只从 `_queue` 移除而不清 `_tasks` 的话，
    `active_task_count()` 会永久虚高，列表端点的 `subtitle_task_active` 会让这一行
    在 UI 上**仍然显示为可取消**（点了又失败），形成鬼影入口。
    """
    with _lock:
        st = _tasks.get(course_id)
        if st is None:
            return None
        if st.status == PENDING:
            if course_id in _queue:
                _queue.remove(course_id)
            # 清 runtime（不能只清队列）：残留的 TaskState 会让 active_task_count() 虚高，
            # 且这一行在前端仍是"可取消"的鬼影入口。
            _tasks.pop(course_id, None)
            _cancel_requested.discard(course_id)
            for i, cid in enumerate(_queue):
                if cid in _tasks:
                    _tasks[cid].queue_position = i
            return "queued"
        if st.status == GENERATING:
            _cancel_requested.add(course_id)
            return "generating"
        return None


def active_task_count() -> int:
    with _lock:
        return sum(1 for st in _tasks.values() if st.status in (PENDING, GENERATING))


def enqueue(course_id: str, video_path: str) -> dict:
    """将字幕生成任务入队（串行）。

    ⚠️ 调用方（admin_materials）**不要用返回值的 status 判断 DB 该写什么**——
    新任务入队后 worker 尚未启动，这里恒返回 PENDING；DB 应无条件写 "generating"
    （v8 修复的 root-cause，见 PRD §5.5A.3 备注）。排队位次看 queue_position。
    """
    st = _get_state(course_id)
    with _lock:
        if st.status == GENERATING:
            return {
                "status": GENERATING,
                "message": "字幕生成中",
                "queue_position": st.queue_position,
            }
        if course_id in _queue:
            return {
                "status": PENDING,
                "message": "已在队列中",
                "queue_position": _queue.index(course_id),
            }
        st.video_path = video_path       # A1：每次入队都用最新传入的路径
        _cancel_requested.discard(course_id)  # 新任务清掉旧的取消标记
        st.status = PENDING
        st.progress = 0.0
        st.slices_done = 0
        st.slices_total = 0
        st.phase = None
        st.started_at = None
        st.error = None
        _queue.append(course_id)
        queue_position = _queue.index(course_id)
    global _worker_running
    if not _worker_running:
        _start_worker()
    return {
        "status": PENDING,
        "message": "已加入字幕生成队列",
        "queue_position": queue_position,
    }


def _start_worker():
    """启动后台 worker（A1：不再需要传 video_path 闭包，bug1 修复）。"""
    global _worker_running
    _worker_running = True
    t = threading.Thread(target=_worker_loop, daemon=True)
    t.start()


def _dequeue(course_id: str) -> None:
    """把 course_id 移出队首 —— 若它还在队列里的话。

    ⚠️ 调用方必须已持有 `_lock`。

    不能无脑写 `_queue.pop(0)`：`cancel()` 的 pending 分支会把任务从队列里
    `remove()` 掉，若此时 worker 正持有该任务且队列已被清空，`pop(0)` 抛
    `IndexError`。**这个异常发生在 daemon 线程里，会直接杀死 worker**，
    而 `_worker_running` 仍是 True → `enqueue()` 之后不再启动新 worker →
    **整条字幕生成队列永久瘫痪，直到进程重启**。
    """
    if _queue and _queue[0] == course_id:
        _queue.pop(0)
    for i, cid in enumerate(_queue):
        _get_state(cid).queue_position = i


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
            # 在持锁窗口内判定：cancel() 的 pending 分支可能刚把本任务置成 ERROR
            # 并从队列里 remove 掉。若这里不检查就往下走，下一行会把它"救活"成
            # GENERATING 并跑完全程 —— 用户看到「已取消」后又变回「已生成」。
            # 状态必须在同一个持锁段内从 PENDING 翻到 GENERATING，一旦中间释放锁
            # 就会留出这个竞态窗口。
            if st.status == ERROR:
                _dequeue(course_id)
                continue
            st.status = GENERATING
            st.progress = 0.0
            # 任务仍留在队首（处理完成/失败后才由 _dequeue 摘掉），这里只刷新位次
            for i, cid in enumerate(_queue):
                _get_state(cid).queue_position = i
        st = _get_state(course_id)
        video_path = st.video_path        # A1 bug1：从 TaskState 取（之前用启动时的闭包变量）
        if not video_path:
            with _lock:
                st.status = ERROR
                st.error = "缺少 video_path"
            _write_back_to_db(
                course_id=course_id, outcome="failed", error="缺少 video_path"
            )
            with _lock:
                _dequeue(course_id)
            continue
        try:
            vtt_path = _run_whisper(course_id, video_path, st)
            # 检测点 ⑦：写 DB ready 前的最后一道闸（PRD §5.5A.3 的 7 个检测点）。
            # 不拦住的话，用户在最后 0.1 秒点的取消会被随后的成功写回覆盖 ——
            # 他刚看到「已取消」，转头又变回「已生成」，比干脆不响应取消更糟。
            _check_cancel(course_id)
            with _lock:
                st.status = READY
                st.progress = 1.0
            _write_back_to_db(course_id=course_id, outcome="success", vtt_path=vtt_path)
        except _CancelledError:
            # 生成中途取消：保留已完成片的 .part（供续跑），DB 回到 pending。
            # **取消不是失败**：不留 error、不留任何长期标记（PRD §5.5A.3「取消不是业务状态」）。
            try:
                _write_back_to_db(course_id=course_id, outcome="cancelled")
            except Exception:  # noqa: BLE001
                pass
            with _lock:
                # runtime 任务一并清掉：留着它就成了「DB 说 pending、task_exists() 说 True」
                # 的自相矛盾状态，且 active_task_count() 会虚高。
                _tasks.pop(course_id, None)
        except Exception as e:  # noqa: BLE001
            with _lock:
                st.status = ERROR
                st.error = str(e)
            try:
                # outcome="failed"：这是真失败，不能和"用户主动取消"混为一谈，
                # 否则管理员在 UI 上无法区分「跑挂了」和「我自己停的」。
                _write_back_to_db(course_id=course_id, outcome="failed", error=str(e))
            except Exception:  # noqa: BLE001
                # DB 也挂了不二次崩；st.error 已记录，不会再让用户以为卡在 generating
                pass
        finally:
            with _lock:
                _dequeue(course_id)
                # 取消标记必须清掉：enqueue() 只在重新入队时 discard，
                # 若该素材之后不再生成，脏标记会永久留在集合里，
                # 让下一次同 course_id 的任务一开始就"被取消"。
                _cancel_requested.discard(course_id)


# CTranslate2 在 Windows 上跑 CUDA 必需的运行库（它自带 cudnn64_9.dll，但**不自带** cuBLAS）。
# 这些 DLL 由 pip 包 `nvidia-cublas-cu12` / `nvidia-cuda-runtime-cu12` 提供，
# 装完躺在 `site-packages/nvidia/*/bin/` 里 —— Python 3.8+ 不再搜索 PATH，
# 不显式注册的话 CTranslate2 就会报 "Library cublas64_12.dll is not found or cannot be loaded"。
CUDA_REQUIRED_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll")


def _register_cuda_dll_dirs() -> list[str]:
    """把 `nvidia/*/bin` 注册进 DLL 搜索路径。**幂等**，可在 import ctranslate2 之前反复调用。

    两个动作一起做，因为二者覆盖了不同的加载路径：
      - `os.add_dll_directory()`：影响本进程后续的 `LoadLibrary`（Python 3.8+ 的推荐做法）；
      - 追加 `PATH`：影响**被加载 DLL 的依赖解析**（依赖 DLL 不走 add_dll_directory）。
    """
    if os.name != "nt":
        return []
    registered: list[str] = []
    for base in sys.path:
        if not base:
            continue
        for d in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
            abs_dir = os.path.abspath(d)
            if abs_dir in registered:
                continue
            try:
                os.add_dll_directory(abs_dir)  # type: ignore[attr-defined]
            except (OSError, AttributeError):
                pass
            registered.append(abs_dir)
    if registered:
        os.environ["PATH"] = os.pathsep.join(registered) + os.pathsep + os.environ.get("PATH", "")
    return registered


def _cuda_is_usable() -> tuple[bool, str]:
    """真实探测 CUDA 能不能用，**不能只信 `get_cuda_device_count()`**。

    `get_cuda_device_count() > 0` 只说明驱动报告有设备。本机（RTX 4070）实测：
    它返回 1，但缺 `cublas64_12.dll`，真正推理时 CTranslate2 抛
    "Library cublas64_12.dll is not found or cannot be loaded"，
    更糟的是某些路径下它会**静默挂起**（CPU 0%、无异常），表现为任务永远卡在"生成中"。

    所以这里补两道真实检查：①设备数 > 0 ②三个必需 DLL 都能被 ctypes 真正加载。
    返回 `(可用?, 不可用原因)` —— 原因要能直接指导修复。
    """
    _register_cuda_dll_dirs()
    try:
        import ctranslate2
    except Exception as e:  # noqa: BLE001
        return False, f"ctranslate2 不可用：{type(e).__name__}: {e}"
    try:
        if ctranslate2.get_cuda_device_count() <= 0:
            return False, "未检测到 CUDA 设备（get_cuda_device_count() == 0）"
    except Exception as e:  # noqa: BLE001
        return False, f"CUDA 设备探测失败：{type(e).__name__}: {e}"

    if os.name == "nt":
        import ctypes

        missing = []
        for dll in CUDA_REQUIRED_DLLS:
            try:
                ctypes.WinDLL(dll)
            except OSError:
                missing.append(dll)
        if missing:
            return False, (
                "CUDA 运行库缺失或无法加载："
                + "、".join(missing)
                + "。请在 backend/venv 执行：pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12"
            )
    return True, ""


def _resolve_device(requested: str) -> str:
    """解析实际推理设备。

    - `cpu`  → 直接返回（显式降级，不做任何 GPU 探测）
    - `cuda` → **必须**可用；不可用则抛 RuntimeError（fail fast，绝不静默挂起）
    - `auto` → 能用 GPU 就用，不能就退回 CPU（不抛）

    ⚠️ 为什么 `cuda` 不再静默退回 CPU：退回会让用户在"以为用 GPU"的情况下
    以 1/6 的速度跑完，且**永远不知道 GPU 其实坏了**。宁可让任务明确失败并报出缺失依赖。
    """
    if requested == "cpu":
        return "cpu"
    ok, reason = _cuda_is_usable()
    if ok:
        return "cuda"
    if requested == "cuda":
        raise RuntimeError(
            f"已配置 whisper_device=cuda，但 GPU 不可用：{reason}。"
            "如需临时降级到 CPU，请设置环境变量 WHISPER_DEVICE=cpu 后重启后端。"
        )
    return "cpu"  # auto：静默退回，这是 auto 的语义


def describe_runtime() -> dict:
    """给管理台展示用：**实际**解析出的设备/精度 + GPU 可用性，不硬编码任何值。

    旧实现把 model 硬编码成 "medium"（真实配置是 small），排查时直接把人带偏。
    """
    device = compute = None
    reason: str | None = None
    try:
        device = _resolve_device(settings.whisper_device)
        compute = _resolve_compute_type(device, settings.whisper_compute_type)
    except Exception as e:  # noqa: BLE001 — 端点不该 500，但要把原因如实报出来
        reason = str(e)
    usable, usable_reason = _cuda_is_usable()
    return {
        "model": settings.whisper_model_size,
        "device": device,
        "compute_type": compute,
        "cuda_usable": usable,
        "cuda_reason": usable_reason or reason,
        "ffmpeg_available": is_ffmpeg_available(),
        "active_tasks": active_task_count(),
    }


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


def _normalize_terms(terms) -> list[str]:
    """术语清洗：去空白 → 丢空串 → **去重（大小写不敏感，保留首次出现的写法）** → 截断到上限。

    去重是硬性要求：`["Spring", "Java", "Spring"]` 若原样拼进 prompt，
    Spring 会在 decoder 上下文里重复出现两次，白白吃掉本就不宽裕的 223 token 窗口，
    也让"术语表"看起来像在刷权重。
    """
    picked: list[str] = []
    seen: set[str] = set()
    for raw in terms or ():
        term = (raw or "").strip()
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        picked.append(term)
        if len(picked) >= MAX_PROMPT_TERMS:
            break
    return picked


def course_terms_for(course_name: str | None) -> list[str]:
    """按课程名取术语表。**未登记 / 不相关的课程返回 `[]`（不套用别的课的词表）。**

    精确登记优先（`COURSE_TERMS`），否则按 `_COURSE_TERM_KEYWORDS` 做同系列判定。
    返回空列表是**正常结果**，不是错误：无关课程被塞进 SSM 术语只会污染识别。
    """
    name = (course_name or "").strip()
    if not name:
        return []
    if name in COURSE_TERMS:
        return list(COURSE_TERMS[name])
    low = name.casefold()
    if any(kw in low for kw in _COURSE_TERM_KEYWORDS):
        return list(_JAVA_SSM_TERMS)
    return []


def _estimate_tokens(text: str) -> int:
    """粗估 token 数（**不加载分词器**，成本可忽略）。

    系数来自对本机 small 分词器的实测校准：
      - CJK：约 0.92 token/字 → 取 **1.0**（宁高勿低）
      - 其余（英文术语 / 顿号 / 标点）：实测 176 字符 ≈ 125 token → 取 **0.7**
    校准样本：23 词 prompt（211 字符）估算 158 / 实测 160；60 字中文尾部估算 60 / 实测 55。

    ⚠️ 这是**估算**，不是真分词。它只需要保证"不低估到让整段 prompt 越过 223 硬上限"，
    为此 CJK 侧刻意取了上界。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk + int((len(text) - cjk) * 0.7)


def _fit_text(text: str, budget: int) -> str:
    """按估算把 text 裁进 token 预算，**保留尾部**（最近的语流最有用）。"""
    if budget <= 0 or not text:
        return ""
    if _estimate_tokens(text) <= budget:
        return text
    per_char = _estimate_tokens(text) / len(text)
    keep = int(budget / per_char) if per_char > 0 else 0
    out = text[-keep:] if keep > 0 else ""
    # 估算有误差：真的还超就从头部再削（最多 8 轮，防死循环）
    for _ in range(8):
        if _estimate_tokens(out) <= budget:
            break
        out = out[1:]
    return out


def _render_prompt(name: str, picked: list[str]) -> str:
    if not picked:
        # 没有术语也要给一个非空 prompt：language 之外再给一句"这是中文课程"，
        # 成本为零，且保证调用方拿到的永远是可直接传给 transcribe 的字符串。
        return f"这是一门中文技术课程：《{name}》。" if name else "这是一门中文课程。"
    head = f"这是一门中文技术课程：《{name}》。" if name else "这是一门中文技术课程。"
    return f"{head}\n请准确识别以下技术术语和英文名称：\n" + "、".join(picked)


def build_whisper_initial_prompt(
    course_name: str | None = None,
    terms: list[str] | None = None,
) -> str:
    """构建 transcribe() 的 initial_prompt：**只说明课程是什么 + 术语的标准写法**。

    `terms=None`（默认）→ **按课程名解析**（`course_terms_for`），未登记的课程 → 无术语；
    `terms=[]` → 明确"没有术语"。两者都落到兜底句，但语义不同：前者是"这门课没登记词表"，
    后者是"调用方说不需要"。

    刻意**不写成长篇自然语言**（不要求改写 / 总结 / 补全讲师没说过的话）：
    initial_prompt 是 ASR 输入提示，不是指令工程。它的唯一作用是给 decoder
    一个"这些写法是这个课程的常态"的先验；写多了反而会把术语的权重稀释掉。

    ⚠️ 只喂"课程名 + 术语"，**不喂** PPT 全文 / 大纲 / 旧字幕：
    那些内容会占满窗口（见 TOKEN_BUDGET），也会让本轮的 A/B 结论
    分不清提升来自术语提示还是来自长文本上下文。

    ⚠️ 超预算时**从表尾裁术语**（低优先级的扩展词先牺牲，核心词留到最后）。
    """
    picked = _normalize_terms(course_terms_for(course_name) if terms is None else terms)
    name = (course_name or "").strip()[:MAX_COURSE_NAME_CHARS]
    # 术语表**自身**必须先进预算（此时还没拼尾部上下文）
    while len(picked) > 1 and _estimate_tokens(_render_prompt(name, picked)) > TOKEN_BUDGET:
        picked.pop()
    return _render_prompt(name, picked)


def _compose_slice_prompt(course_prompt: str, tail: str) -> str:
    """把「课程术语提示」和「上一片尾部上下文」合成单片的 initial_prompt。

    优先级：**术语 > 尾部上下文**。理由：术语表一旦被 `get_prompt()` 从头部砍掉，
    本轮优化就归零；尾部上下文只是"续接语流"，少几个字损失很小。

    两级裁剪：① 字符硬上限 `MAX_SLICE_TAIL_CHARS`；② token 预算 —— 术语表占掉多少，
    尾部就只剩多少（见 TOKEN_BUDGET）。截尾部的**头**，保留最近的语流。
    """
    course_prompt = (course_prompt or "")[:MAX_INITIAL_PROMPT_CHARS]
    tail = (tail or "").strip()[-MAX_SLICE_TAIL_CHARS:]
    if not course_prompt:
        return tail
    if not tail:
        return course_prompt
    # 术语表自身已超预算时（build 阶段没裁干净 / 手工传入超长 prompt），
    # 尾部直接让位——不能为了续接语流把术语挤没了。
    left = TOKEN_BUDGET - _estimate_tokens(course_prompt) - 1
    tail = _fit_text(tail, left)
    if not tail:
        return course_prompt
    merged = f"{course_prompt}\n{tail}"
    # `_estimate_tokens` 有取整误差（分段估 ≠ 整体估），按**整体**再校一次；
    # tail ≤ 60 字，逐字削的开销可忽略，换来的是"绝不越过 TOKEN_BUDGET"。
    while tail and _estimate_tokens(merged) > TOKEN_BUDGET:
        tail = tail[1:]
        merged = f"{course_prompt}\n{tail}"
    return merged if tail else course_prompt


def _write_atomic(path: Path, text: str) -> None:
    """原子写盘：先写 .tmp 再 replace，防止写一半断电留下坏文件（接手文档 5.1）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_part_atomic(path: Path, cues: list[dict]) -> None:
    _write_atomic(path, json.dumps(cues, ensure_ascii=False))


def _read_part(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _check_cancel(course_id: str) -> None:
    """取消标记已置位就抛 `_CancelledError`。

    取消是用户主动终止，**不是失败** —— 调用方不得把它当异常吞掉后继续跑完。
    """
    if _is_cancel_requested(course_id):
        raise _CancelledError(course_id)


def _iter_segments(segments, course_id: str):
    """边消费 faster-whisper 的 segments 边检测取消。

    `segments` 是**生成器（lazy）** —— 不消费就不计算。所以"停止消费"就是真正的中断。
    这是单片场景下唯一能在转写**中途**停下来的手段：视频 ≤60s 或时长探测失败时
    `n_slices == 1`，切片循环只跑一次、且检测发生在转写**之前**，
    仅靠切片间的检测点会让"取消"在整段转写期间完全失灵。
    """
    for seg in segments:
        _check_cancel(course_id)
        yield seg


def _transcribe_slice(
    model,
    slice_path: str,
    initial_prompt: str,
    language: str | None,
    retries: int,
    course_id: str,
) -> list[dict]:
    """转写单个切片文件（相对时间戳 0..slice_sec）。失败按 retries 重试。

    ⚠️ `_CancelledError` **穿透重试**：取消不是转写失败，被 retry 吞掉继续重试
    等于取消失灵（原本会一路重试到上限，然后照常产出字幕）。
    """
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            segs, _info = model.transcribe(
                slice_path,
                # 显式指定语言，不接受 None：留空配置也回落到 zh。
                # 中英混合技术课程靠自动检测会被整段判成 en/ja（见 DEFAULT_LANGUAGE 注释）。
                language=language or DEFAULT_LANGUAGE,
                vad_filter=True,
                beam_size=5,
                initial_prompt=initial_prompt or None,
            )
            return [
                {"start": s.start, "end": s.end, "text": (s.text or "").strip()}
                for s in _iter_segments(segs, course_id)
            ]
        except _CancelledError:
            raise
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
    # ⚠️ 必须在 import faster_whisper（它会 import ctranslate2）**之前**注册
    # `nvidia/*/bin`：CTranslate2 的 CUDA 运行库在 site-packages 里，Python 3.8+ 不搜 PATH，
    # 不注册就会在真正推理时报 "Library cublas64_12.dll is not found or cannot be loaded"。
    # 函数幂等，重复调用无副作用。
    _register_cuda_dll_dirs()
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError("未安装 faster-whisper，请先 pip install faster-whisper") from e

    # v9 §5.5A.4：**进入任何耗时步骤之前**就公开"已开跑"。
    # 首次 WhisperModel(...) 会下载模型（可达数分钟），此时既没有 slices_total 也没有
    # 转写进度 —— 若把 started_at 推迟到模型加载之后，这段窗口内前端降级轨连计时都没有，
    # 只剩孤零零一行「⏳ 正在生成字幕…」，与卡死无法区分。
    # phase="loading_model" 让文案变成"正在加载模型…"——此刻显示"正在生成字幕"是在撒谎。
    with _lock:
        st.started_at = time.time()
        st.phase = "loading_model"

    device = _resolve_device(settings.whisper_device)
    compute_type = _resolve_compute_type(device, settings.whisper_compute_type)
    model = WhisperModel(
        settings.whisper_model_size,
        device=device,
        compute_type=compute_type,
    )

    # 课程术语提示词：course_id 就是目录名（如「001.AI版SSM教程简介」），可直接当课程名用。
    # 不查库、不加字段 —— 复用已有信息即可，术语表是 Demo 级固定常量。
    course_prompt = build_whisper_initial_prompt(course_name=course_id)
    language = (settings.whisper_language or "").strip() or DEFAULT_LANGUAGE

    ffmpeg = resolve_ffmpeg()
    duration = _probe_duration(video_path)
    slice_sec = SLICE_SEC
    n_slices = max(1, math.ceil(duration / slice_sec)) if duration > 0 else 1
    base = Path(video_path).with_suffix("")
    part_paths = [base.with_name(f"{base.name}.whisper.slice{i}.part") for i in range(n_slices)]

    all_cues: list[dict] = []
    prev_tail = ""
    done = 0
    # 时长探测很快，拿到总数就切回正常轨（百分比 + 切片计数）。
    with _lock:
        st.slices_total = n_slices
        st.slices_done = 0
        st.phase = "transcribing"

    for i in range(n_slices):
        _check_cancel(course_id)                              # ① 切片间
        if part_paths[i].exists():
            slice_cues = _read_part(part_paths[i])            # 已完成片：直接复用（断点续跑）
        else:
            slice_path = _slice_media(video_path, i, slice_sec, ffmpeg) if (ffmpeg and n_slices > 1) else video_path
            try:
                slice_cues = _transcribe_slice(
                    model, slice_path, _compose_slice_prompt(course_prompt, prev_tail),
                    language, SLICE_RETRIES, course_id,
                )
            finally:
                if slice_path != video_path:
                    Path(slice_path).unlink(missing_ok=True)   # 删临时切片媒体
            _write_part_atomic(part_paths[i], slice_cues)      # 落盘（续跑状态）
            # ② 本片转写结束后再补检一次。
            #
            # ⚠️ 诚实的说明：**这一处在当前控制流下是冗余的**。变异测试证实 ——
            #   多片场景由检测点①（下一轮循环开头）捕获，单片场景由检测点③（合并前）捕获，
            #   去掉本行所有取消测试依然全绿。保留它是为了让"每个阶段边界都有检测点"
            #   这条规则成立：将来若在落盘与合并之间插入任何耗时步骤，它就是唯一屏障。
            #   不要因为它看起来没用就在重构时顺手删掉，也不要误以为它是关键兜底。
            #
            # 放在落盘**之后**：这一片已经转完了，应当保留供续跑
            # （与 _CancelledError 分支"保留已完成片的 .part"语义一致）。
            # 放在落盘之前会把刚转完的整片白扔掉，长视频下等于浪费几十分钟算力。
            _check_cancel(course_id)
        shifted = [
            {"start": c["start"] + i * slice_sec, "end": c["end"] + i * slice_sec, "text": c["text"]}
            for c in slice_cues
        ]
        all_cues.extend(shifted)
        prev_tail = _tail_text(slice_cues)
        done += 1
        with _lock:
            st.slices_done = done
            st.progress = done / n_slices

    # v8 §5.5A.3：切片跑完 → 进入合并阶段（前端显示"合并字幕…"）。
    # 上千条 cue 的 cues_to_vtt + 原子写盘可能要数秒，这段没有切片进度可报，
    # 不标 merging 的话前端会一直停在"生成中 100%"，看起来像卡死。
    with _lock:
        st.phase = "merging"
    # ③④ 合并阶段原本完全没有检测点：上千条 cue 的 cues_to_vtt + 原子写盘要数秒，
    # 这段时间内点取消会眼睁睁看着任务跑完并回写 ready —— 取消看起来像失灵。
    _check_cancel(course_id)                                  # ③ 合并前
    vtt_content = cues_to_vtt(all_cues)
    _check_cancel(course_id)                                  # ④ 合并后、写盘前
    vtt_path = base.with_name(f"{base.name}.whisper.vtt")
    _write_atomic(vtt_path, vtt_content)
    with _lock:
        st.progress = 1.0
        st.phase = None
    for p in part_paths:
        p.unlink(missing_ok=True)   # 合并成功才清状态文件
    return str(vtt_path)


def _write_back_to_db(
    course_id: str,
    outcome: str,
    vtt_path: str | None = None,
    error: str | None = None,
) -> None:
    """A1 bug2 修复：worker 把结果写回 Material 表。

    用独立 SessionLocal（不复用任何请求的 db session），写完即关。

    `outcome` 三选一（PRD §5.5A.3）：

    | outcome | subtitle_status | subtitle_error | 其它字段 |
    |---|---|---|---|
    | `"success"` | `ready` | `None` | 写 `subtitle_path` / `subtitle_source='whisper'` / `subtitle_source_format='vtt'` |
    | `"failed"` | `error` | 失败原因（截到 2000 字防 SQLite 字段超限） | 不动 |
    | `"cancelled"` | `pending` | `None` | **一律不动**（见下） |

    用字符串枚举而不是 `success: bool` + `canceled: bool` 两个正交标志：
    两个布尔能表达 4 种组合，其中 `success=True, canceled=True` 是**无意义的非法组合**，
    却没有任何机制阻止它。三态用一个参数表达，非法状态在类型层面就消失了。

    ⚠️ `cancelled` 分支**不动 `subtitle_path` / `subtitle_source`**：
    已完成的 `.part` 要留着供续跑，而"上次生成到一半被取消"这件事
    不构成对成品字幕的任何修改。清掉 path 会让管理员以为字幕被删了。

    ⚠️ `cancelled` 回 `pending` 而非 `error`：取消是"这次没生成、随时可以再来"，
    不是"这个素材生成不了"。写 `error` 会让管理员误判为素材有问题（PRD §5.5A.3）。
    已知边界：对**已有字幕的素材**点"重新生成"再取消，会回到 `pending` 而旧字幕仍在磁盘上
    （path 保留），UI 上表现为"待生成"但 Drawer 里看得到旧内容 —— 这是任务说明明确的取舍，
    不是 bug；若将来需要"取消后回到 ready"，应在 `_do_generate_subtitle` 里记录原状态。

    测试入口：SessionLocal 与 Material 已提到模块顶层，测试用
    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: FakeDB()) 替换。
    """
    db = SessionLocal()
    try:
        material = db.query(Material).filter(Material.course_id == course_id).first()
        if material is None:
            # 任务还在跑、Material 被管理员删了——保守放弃写回，不崩
            return
        if outcome == "success" and vtt_path:
            material.subtitle_path = vtt_path
            material.subtitle_source_format = "vtt"
            material.subtitle_source = "whisper"
            material.subtitle_status = "ready"
            material.subtitle_error = None
        elif outcome == "cancelled":
            material.subtitle_status = "pending"
            material.subtitle_error = None
            # subtitle_path / subtitle_source 不动（见 docstring）
        else:
            material.subtitle_status = "error"
            material.subtitle_error = (error or "未知错误")[:2000]
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
