"""字幕生成「取消」与「进度可见性」行为测试（v9 批次 1 · B1-2 / B1-3）。

这里的断言全部针对**业务事实**，不是"抛了个异常就算数"：

- 取消后必须**没有成品 vtt 落盘**。只断言"抛了 _CancelledError"是不够的 ——
  原实现里取消请求在单片/合并阶段永不命中，任务照常跑完并回写 ready，
  那种行为下"抛异常"型断言照样能通过，测试就变成了给 bug 打掩护。
- 单片场景（n_slices == 1）必须同样生效，这是原实现的盲区。
- 已完成片的 .part 必须保留（断点续跑依赖它），成品 vtt 不能有。
- started_at 必须在模型加载**之前**就可读（否则降级轨连计时都没有）。
"""

import sys
from types import SimpleNamespace

import pytest

from app.services import whisper_service
from app.services.whisper_service import TaskState


@pytest.fixture(autouse=True)
def reset_whisper_state():
    """每个测试前后重置全局任务状态（与 test_whisper.py 同一套约定）。"""
    for attr, value in (
        ("_tasks", {}),
        ("_queue", []),
        ("_worker_running", False),
        ("_cancel_requested", set()),
    ):
        if hasattr(whisper_service, attr):
            container = getattr(whisper_service, attr)
            container.clear() if hasattr(container, "clear") else None
        else:  # pragma: no cover - 防御：内部重命名时立刻暴露
            raise AssertionError(f"whisper_service 缺少 {attr}")
    yield
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()


def _fake_fw(
    segments,
    duration: float = 10.0,
    *,
    course_id: str = "c1",
    cancel_at=None,
    on_ctor=None,
    consumed: list | None = None,
):
    """构造 fake faster_whisper 模块。

    `cancel_at` 控制取消置位的时机，用来精确命中不同的检测点：

    | 取值 | 置位时机 | 由哪个检测点捕获 |
    |---|---|---|
    | `"between"` | 转写进行中（产出第 2 个 segment 前） | `_iter_segments` 段间检测（v9 新增） |
    | `"after_all"` | 所有 segment 耗尽、转写刚返回时 | 单片转写后的检测点（v9 新增，n_slices==1 的兜底） |
    | `("after_slice", n)` | 第 n 片转写结束**后** | 「单片转写后」检测点 —— 用于区分它和「合并前」检测点 |
    | `("slice", n)` | 第 n 次 transcribe 调用进行中 | `_iter_segments` 段间检测 |

    注意：置位必须发生在 `_run_whisper` 的「切片间」检测点**之后**，
    否则会被那个原有检测点先捕获，测不到新增的检测点是否真的生效。

    `consumed` 若传入列表，会记录生成器实际推进到了第几个 segment ——
    用它区分"转写中途就停了"和"整段转写完才停"。
    """
    import types

    fake = types.ModuleType("faster_whisper")
    info = SimpleNamespace(duration=duration)
    calls = {"n": 0}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            if on_ctor is not None:
                on_ctor()

        def transcribe(self, path, **kwargs):
            calls["n"] += 1
            mine = calls["n"]

            def gen():
                for i, seg in enumerate(segments):
                    if cancel_at == "between" and i >= 1:
                        whisper_service._cancel_requested.add(course_id)
                    elif isinstance(cancel_at, tuple) and cancel_at[0] == "slice":
                        _, nth = cancel_at
                        if mine == nth and i >= 1:
                            whisper_service._cancel_requested.add(course_id)
                    if consumed is not None:
                        consumed.append(i)
                    yield SimpleNamespace(start=seg[0], end=seg[1], text=seg[2])
                # segments 耗尽 = 本片转写结束
                if cancel_at == "after_all" or (
                    isinstance(cancel_at, tuple)
                    and cancel_at[0] == "after_slice"
                    and mine == cancel_at[1]
                ):
                    whisper_service._cancel_requested.add(course_id)

            return gen(), info

    fake.WhisperModel = FakeModel
    return fake


def _patch_env(monkeypatch, fake_module, duration: float):
    """统一 mock：不真调 ffmpeg、不真探测时长（时长决定 n_slices）。"""
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    # resolve_ffmpeg 返回 None → 不调 _slice_media，直接用原文件转写。
    # 这让测试只关心取消行为，不依赖本机是否装了 ffmpeg。
    monkeypatch.setattr(whisper_service, "resolve_ffmpeg", lambda: None)
    monkeypatch.setattr(whisper_service, "_probe_duration", lambda p: duration)


# ---------- B1-2：取消必须真的停下来 ----------


def test_cancel_mid_transcribe_stops_before_writing_vtt(monkeypatch, tmp_path):
    """转写进行中取消 → 抛 `_CancelledError`，且**没有成品 vtt 落盘**。"""
    _patch_env(
        monkeypatch,
        _fake_fw(
            [(0.0, 2.0, "a"), (2.0, 5.0, "b"), (5.0, 8.0, "c")],
            duration=10.0,
            cancel_at="between",
        ),
        duration=30.0,
    )

    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    st = TaskState(course_id="c1")

    with pytest.raises(whisper_service._CancelledError):
        whisper_service._run_whisper("c1", str(video), st)

    assert not (tmp_path / "v.whisper.vtt").exists(), "取消后不应产出成品字幕"
    assert st.phase != "merging", "取消发生在转写阶段，不该走到合并"


def test_single_slice_cancel_is_honoured(monkeypatch, tmp_path):
    """`n_slices == 1` 时，转写**结束后**取消仍生效。

    这是原实现的盲区：视频 ≤60s 或时长探测失败时 `n_slices` 为 1，
    切片循环只跑一次、且检测点在转写**之前** —— 转写这几十分钟里
    取消永不命中，任务照常跑完并回写 ready，管理员看着取消按钮像失灵。
    """
    _patch_env(
        monkeypatch,
        _fake_fw([(0.0, 2.0, "只有一段")], duration=10.0, cancel_at="after_all"),
        duration=0.0,  # 探测失败 → n_slices = 1
    )

    video = tmp_path / "short.mp4"
    video.write_bytes(b"fake")
    st = TaskState(course_id="c1")

    with pytest.raises(whisper_service._CancelledError):
        whisper_service._run_whisper("c1", str(video), st)

    assert not (tmp_path / "short.whisper.vtt").exists(), "单片场景取消同样不该产出成品"


def test_cancel_during_merging_does_not_write_vtt(monkeypatch, tmp_path):
    """合并阶段（`cues_to_vtt` 之后、写盘之前）取消 → 不落盘。

    原实现 merging 阶段完全没有检测点。上千条 cue 的序列化 + 原子写盘要数秒，
    这段时间内点取消，会眼睁睁看着任务跑完。
    """
    _patch_env(
        monkeypatch,
        _fake_fw([(0.0, 2.0, "a")], duration=10.0),
        duration=30.0,
    )

    original = whisper_service.cues_to_vtt

    def cancel_then_serialize(cues):
        # 模拟"序列化期间用户点了取消"
        whisper_service._cancel_requested.add("c1")
        return original(cues)

    monkeypatch.setattr(whisper_service, "cues_to_vtt", cancel_then_serialize)

    video = tmp_path / "m.mp4"
    video.write_bytes(b"fake")

    with pytest.raises(whisper_service._CancelledError):
        whisper_service._run_whisper("c1", str(video), TaskState(course_id="c1"))

    assert not (tmp_path / "m.whisper.vtt").exists()


def test_cancel_stops_mid_transcription_not_after_it(monkeypatch, tmp_path):
    """单片长转写时，取消应在**转写中途**生效，而不是等整段转写完。

    场景：时长探测失败 → `n_slices == 1` → 整个文件一次转写（可能几十分钟、几百个 segment）。
    若只有"转写后"那个检测点，用户点取消后要干等到整段转写完才停下 ——
    对长视频而言这跟没取消一样。`_iter_segments` 的段间检测是唯一能在中途停下的手段。
    """
    segments = [(float(i), float(i) + 1, f"第{i}句") for i in range(5)]
    consumed: list = []

    _patch_env(
        monkeypatch,
        _fake_fw(
            segments,
            duration=10.0,
            cancel_at="between",
            consumed=consumed,
        ),
        duration=0.0,  # 探测失败 → n_slices = 1，整个文件一次转写
    )

    video = tmp_path / "long_single.mp4"
    video.write_bytes(b"fake")

    with pytest.raises(whisper_service._CancelledError):
        whisper_service._run_whisper("c1", str(video), TaskState(course_id="c1"))

    # 关键断言：转写没跑完就停了。若 removed _iter_segments，这里会是 5（跑完全部）
    assert len(consumed) < len(segments), (
        f"取消应在转写中途生效，实际推进了 {len(consumed)}/{len(segments)} 个 segment"
    )
    assert not (tmp_path / "long_single.whisper.vtt").exists()


def test_cancel_between_slices_stops_next_slice(monkeypatch, tmp_path):
    """多片场景下，某片转完后的取消应阻止**下一片**开始转，而不是等全部转完。

    守护的是检测点①（切片间检测，原有能力），防止将来重构时把它删掉。

    ⚠️ 变异测试结论：本用例**无法**区分检测点②（本片转写后）的存废 ——
    多片场景下检测点① 会先捕获取消。所以别把它当成"检测点② 的测试"，
    检测点② 在功能上与 ①③ 冗余（详见 whisper_service 里的注释）。
    """
    consumed: list = []

    _patch_env(
        monkeypatch,
        _fake_fw(
            [(0.0, 1.0, "a"), (1.0, 2.0, "b")],
            duration=150.0,
            cancel_at=("after_slice", 2),
            consumed=consumed,
        ),
        duration=150.0,  # ceil(150/60) = 3 片
    )

    video = tmp_path / "multi.mp4"
    video.write_bytes(b"fake")

    with pytest.raises(whisper_service._CancelledError):
        whisper_service._run_whisper("c1", str(video), TaskState(course_id="c1"))

    # 每片转 2 个 segment：转完 2 片 = 推进 4 次。若第 3 片也转了会是 6
    assert len(consumed) == 4, (
        f"第 2 片转完就该停下，不该再转第 3 片；实际推进 {len(consumed)} 次"
    )
    assert not (tmp_path / "multi.whisper.vtt").exists()


def test_cancel_preserves_completed_parts(monkeypatch, tmp_path):
    """3 片跑完 2 片后取消：已完成片的 `.part` 保留（能续跑），成品 vtt 不产出。"""
    _patch_env(
        monkeypatch,
        _fake_fw(
            [(0.0, 2.0, "a"), (2.0, 4.0, "b")],
            duration=150.0,
            cancel_at=("slice", 3),
        ),
        duration=150.0,  # ceil(150/60) = 3 片
    )

    video = tmp_path / "long.mp4"
    video.write_bytes(b"fake")
    st = TaskState(course_id="c1")

    with pytest.raises(whisper_service._CancelledError):
        whisper_service._run_whisper("c1", str(video), st)

    # 已完成的两片必须留下 —— 断点续跑依赖它们
    assert (tmp_path / "long.whisper.slice0.part").exists(), "已完成片应保留供续跑"
    assert (tmp_path / "long.whisper.slice1.part").exists(), "已完成片应保留供续跑"
    # 取消发生在第 3 片转写中，该片不落盘
    assert not (tmp_path / "long.whisper.slice2.part").exists()
    assert not (tmp_path / "long.whisper.vtt").exists(), "未跑完不应产出成品"


# ---------- B1-3：进度在模型加载期间就可见 ----------


def test_started_at_available_before_model_load(monkeypatch, tmp_path):
    """在 `WhisperModel` 构造**期间**就能读到 `started_at` 与 `phase='loading_model'`。

    断言的是"前端在模型下载的那几分钟里就能开始计时"这个事实，而不是"字段存在"。
    原实现把 `started_at` 的写入推迟到模型加载之后 —— 首次生成时 Whisper 会下载
    模型（可达数分钟），这段窗口内前端降级轨连计时都没有，只剩一行
    「⏳ 正在生成字幕…」，与卡死无法区分。
    """
    st = TaskState(course_id="c1")
    seen: dict = {}

    def on_ctor():
        # 模拟模型构造/下载耗时期间，前端来轮询
        seen["started_at"] = st.started_at
        seen["phase"] = st.phase
        seen["slices_total"] = st.slices_total

    _patch_env(
        monkeypatch,
        _fake_fw([(0.0, 2.0, "a")], duration=10.0, on_ctor=on_ctor),
        duration=30.0,
    )

    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    whisper_service._run_whisper("c1", str(video), st)

    assert seen["started_at"] is not None, "模型加载期间前端就该能开始计时"
    assert seen["phase"] == "loading_model", "此刻在加载模型，标 transcribing 是撒谎"
    assert seen["slices_total"] == 0, "模型还没加载完，拿不到切片总数（走降级轨）"


# ---------- v9 补丁：worker 与 cancel 的竞态 ----------


def test_worker_does_not_resurrect_a_cancelled_task(monkeypatch):
    """排队中取消后，worker 不能把任务"救活"再跑一遍。

    竞态窗口：worker 取到 `_queue[0]` 后释放锁，此时 cancel() 的 pending 分支
    把任务置 ERROR 并从队列移除；worker 若不检查就继续，会把状态改回 GENERATING
    并跑完全程 —— 用户刚看到「已取消」，过一会儿又变回「已生成」。

    断言的是业务事实：**转写一次都没发生**，而不是"没抛异常"。
    """
    ran = []

    def fake_run(course_id, video_path, st):
        ran.append(course_id)
        return "/tmp/out.vtt"

    monkeypatch.setattr(whisper_service, "_run_whisper", fake_run)
    monkeypatch.setattr(whisper_service, "_write_back_to_db", lambda **kw: None)

    st = TaskState(course_id="c1", video_path="/A.mp4")
    st.status = whisper_service.ERROR
    st.error = "已取消"
    whisper_service._tasks["c1"] = st
    whisper_service._queue.append("c1")  # 模拟"已取消但仍在队列里"的竞态瞬间
    whisper_service._worker_running = True

    whisper_service._worker_loop()

    assert ran == [], "已取消的任务不该被 worker 重新跑起来"
    assert st.status == whisper_service.ERROR, "状态不能被翻回 generating"
    assert whisper_service._queue == [], "已取消的任务必须出队，否则队列永久卡住"


def test_worker_survives_queue_drained_by_concurrent_cancel(monkeypatch):
    """任务处理期间队列被并发清空时，worker 不能被 IndexError 杀死。

    这是本补丁修复的最严重后果：`finally` 里无脑 `_queue.pop(0)`，
    而 cancel() 可能已经把任务 remove 掉了。异常抛在 daemon 线程里会直接杀死
    worker，但 `_worker_running` 仍是 True → `enqueue()` 之后不再启动新 worker →
    **整条字幕生成队列永久瘫痪，直到进程重启**。

    所以这里断言的不是"没抛异常"（pytest 本身就捕获线程外异常），
    而是 worker 能正常收尾并退出循环。
    """

    def fake_run(course_id, video_path, st):
        # 模拟并发取消：转写期间任务被移出队列并置 ERROR
        whisper_service._queue.clear()
        st.status = whisper_service.ERROR
        return "/tmp/out.vtt"

    monkeypatch.setattr(whisper_service, "_run_whisper", fake_run)
    monkeypatch.setattr(whisper_service, "_write_back_to_db", lambda **kw: None)

    whisper_service._tasks["c1"] = TaskState(course_id="c1", video_path="/A.mp4")
    whisper_service._queue.append("c1")
    whisper_service._worker_running = True

    whisper_service._worker_loop()  # 不抛 IndexError；pop 前必须先看队首是不是自己

    assert whisper_service._queue == []
    assert whisper_service._worker_running is False, "队列空后 worker 应正常收尾退出"


def test_cancel_before_writing_ready_is_honored(monkeypatch):
    """检测点 ⑦：转写已跑完、但还没写 DB `ready` 之前，取消必须仍然生效。

    这是 7 个检测点里的最后一道闸。不拦住的话，用户在最后 0.1 秒点的取消
    会被随后的成功写回覆盖 —— 他刚看到「已取消」，转头又变回「已生成」，
    比干脆不响应取消更糟（PRD §5.5A.3）。
    """
    written: list[dict] = []

    def fake_run(course_id, video_path, st):
        # 模拟"转写刚跑完、管理员此刻点了取消"
        whisper_service._cancel_requested.add(course_id)
        return "/tmp/out.vtt"

    monkeypatch.setattr(whisper_service, "_run_whisper", fake_run)
    monkeypatch.setattr(
        whisper_service, "_write_back_to_db", lambda **kw: written.append(kw)
    )

    whisper_service._tasks["c1"] = TaskState(course_id="c1", video_path="/A.mp4")
    whisper_service._queue.append("c1")
    whisper_service._worker_running = True

    whisper_service._worker_loop()

    assert written, "取消也要有收尾写回（DB 不能停在 generating）"
    assert all(w["outcome"] != "success" for w in written), (
        "转写跑完后、写 DB ready 前检测到取消 —— 绝不能把这次写成 success"
    )
    assert written[-1]["outcome"] == "cancelled"


def test_cancelled_run_keeps_completed_part_files(monkeypatch, tmp_path):
    """生成中取消：已完成切片的 `.part` 必须保留，供下次续跑。

    `.part` 是"文件系统即状态"的续跑载体（每片 JSON cues）。
    取消时删掉它们，等于让重新生成从零开始 —— 半小时的长视频白跑一半。
    """
    base = tmp_path / "v.mp4"
    base.write_bytes(b"fake")
    parts = [base.with_name(f"{base.name}.whisper.slice{i}.part") for i in range(3)]
    for i, p in enumerate(parts[:2]):      # 模拟已完成前 2 片
        p.write_text(f'[{{"start": {i}, "end": {i + 1}, "text": "x"}}]', encoding="utf-8")

    def fake_run(course_id, video_path, st):
        raise whisper_service._CancelledError(course_id)

    monkeypatch.setattr(whisper_service, "_run_whisper", fake_run)
    monkeypatch.setattr(whisper_service, "_write_back_to_db", lambda **kw: None)

    whisper_service._tasks["c1"] = TaskState(course_id="c1", video_path=str(base))
    whisper_service._queue.append("c1")
    whisper_service._worker_running = True

    whisper_service._worker_loop()

    assert parts[0].exists() and parts[1].exists(), "已完成的 .part 必须保留供续跑"
    assert not parts[2].exists(), "没跑到的第 3 片本来就没有 .part"
    assert "c1" not in whisper_service._cancel_requested, "收尾必须清掉取消标记"
