"""small + zh + initial_prompt：课程术语提示词测试（不真实加载模型）。

守的是本轮唯一的变量 —— **往 transcribe() 里塞了什么**：

1. `language="zh"` 必须显式传入（不再依赖自动语言检测）；
2. `initial_prompt` 必须**非空且真的传到** transcribe()（旧版首片传的是 None，
   因为首片没有"上一片尾部上下文"，prompt 直接被 `or None` 吞掉）；
3. 术语表必须包含当前 Java/SSM 课程的标准拼写；
4. 术语去重（重复项会白占 decoder 上下文）；
5. 没有术语时不能崩；
6. 加了 prompt 之后状态机照旧：ready + unreviewed + whisper
   —— Prompt 提升准确率 ≠ 人工审核完成。

⚠️ 全部用例都用 fake faster_whisper，**不加载真实模型、不访问真实 app.db**
（device 也直接钉死为 cpu，避免依赖本机 GPU 是否存在）。
"""

import sys

import pytest

from app.core.config import settings
from app.models.models import Material
from app.services import whisper_service
from app.services.whisper_service import TaskState
from tests.test_admin_materials import _h, client  # noqa: F401 — 复用 client fixture
from tests.test_whisper import _make_fake_faster_whisper


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """钉死设备 + 清全局任务状态，保证用例不碰 GPU、不串状态。"""
    monkeypatch.setattr(whisper_service, "_resolve_device", lambda _requested: "cpu")
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()
    yield
    whisper_service._tasks.clear()
    whisper_service._queue.clear()
    whisper_service._worker_running = False
    whisper_service._cancel_requested.clear()


def _run_once(monkeypatch, tmp_path, recorded, course_id="001.AI版SSM教程简介"):
    """跑一次 _run_whisper 并把 transcribe() 的 kwargs 记进 recorded[1]。

    默认 course_id 用真实课程名：`course_terms_for()` 只对**登记/相关课程**给词表，
    用 "c1" 这种无关 id 会拿不到术语，测不到本轮要守的东西。
    """
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        _make_fake_faster_whisper([(0.0, 1.0, "你好")], recorded=recorded),
    )
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    whisper_service._run_whisper(course_id, str(video), TaskState(course_id=course_id))
    return recorded[1]


# ---------- Test 1：固定中文 ----------


def test_transcribe_always_gets_language_zh(monkeypatch, tmp_path):
    """Test 1：transcribe() 必须拿到 language="zh"。

    断言**字面量 "zh"** 而不是 `settings.whisper_language`：配置一旦被清空就
    退回自动检测，中英混合课程会被整段判成 en/ja —— 这正是本轮要杜绝的。
    """
    recorded = []
    kwargs = _run_once(monkeypatch, tmp_path, recorded)
    assert kwargs.get("language") == "zh"


def test_language_falls_back_to_zh_when_setting_blank(monkeypatch, tmp_path):
    """Test 1 边界：`whisper_language=""`（旧语义＝自动检测）也必须回落成 zh。"""
    monkeypatch.setattr(settings, "whisper_language", "")
    recorded = []
    kwargs = _run_once(monkeypatch, tmp_path, recorded)
    assert kwargs.get("language") == "zh"


# ---------- Test 2：initial_prompt 真的传进去了 ----------


def test_initial_prompt_actually_reaches_transcribe(monkeypatch, tmp_path):
    """Test 2：initial_prompt 非空，且原样传到 transcribe()。

    旧代码首片传 `prev_tail`（空串）→ `initial_prompt or None` → **None**，
    也就是说"提示词"只在第 2 片之后才存在。这是本轮要修掉的隐性行为。
    """
    recorded = []
    kwargs = _run_once(monkeypatch, tmp_path, recorded)
    prompt = kwargs.get("initial_prompt")
    assert prompt, "initial_prompt 不得为空/None"
    assert "技术术语" in prompt
    assert "Spring" in prompt


def test_initial_prompt_keeps_slice_tail_within_budget():
    """Test 2 边界：尾部上下文必须给术语表让位（两级裁剪：字符硬上限 → token 预算）。

    实测：23 词 prompt ≈160 token，而 decoder 只保留**最后 223 个 token** ——
    不封顶时术语表会被整段砍掉。
    """
    course_prompt = whisper_service.build_whisper_initial_prompt(course_name="001.AI版SSM教程简介")
    composed = whisper_service._compose_slice_prompt(course_prompt, "上" * 500)
    assert course_prompt in composed, "术语表必须整体保留，一个字都不能裁"
    tail_part = composed.split("\n")[-1]
    assert 0 < len(tail_part) <= whisper_service.MAX_SLICE_TAIL_CHARS
    assert tail_part == "上" * len(tail_part), "保留最近的语流（截头不截尾）"
    assert (
        whisper_service._estimate_tokens(composed) <= whisper_service.TOKEN_BUDGET
    ), "合成后的 prompt 必须进 token 预算"


def test_unrelated_course_still_gets_non_empty_prompt(monkeypatch, tmp_path):
    """Test 2 兼容：未登记词表的课程**不套用** SSM 术语，但 prompt 仍非空。"""
    recorded = []
    kwargs = _run_once(monkeypatch, tmp_path, recorded, course_id="c1")
    assert kwargs["initial_prompt"] == "这是一门中文技术课程：《c1》。"
    assert "Spring" not in kwargs["initial_prompt"], "无关课程不该被塞 SSM 术语"


# ---------- Test 3：术语存在 ----------


def test_prompt_contains_course_terms_and_name():
    """Test 3：当前 Java/SSM 课程的标准术语必须在 prompt 里，且带课程名。"""
    prompt = whisper_service.build_whisper_initial_prompt(course_name="001.AI版SSM教程简介")
    for term in ("Java", "SSM", "Spring", "Spring MVC", "MyBatis", "Spring Boot"):
        assert term in prompt, f"缺少术语 {term}"
    assert "001.AI版SSM教程简介" in prompt


def test_prompt_has_no_misrecognized_spellings():
    """Test 3 反向：识别错误的写法绝不许进词表（那等于教模型把错字当正字）。"""
    prompt = whisper_service.build_whisper_initial_prompt(course_name="001.AI版SSM教程简介")
    for bad in ("Sprin ", "Mapitis", "扎瓦", "上规谷"):
        assert bad not in prompt


# ---------- 词表是「课程专属」，不是全局默认 ----------


def test_terms_are_course_scoped():
    """扩展词表只属于 Java/SSM 系列课程，不相关课程拿不到。

    用户决策：这是**当前课程词表**，不能当成所有课程的全局默认 ——
    给无关课程塞 SSM 术语只会污染识别。
    """
    assert whisper_service.course_terms_for("001.AI版SSM教程简介"), "登记课程必须有词表"
    assert whisper_service.course_terms_for("spring-ioc-005"), "同系列课程复用词表"
    assert whisper_service.course_terms_for("英语口语-001") == []
    assert whisper_service.course_terms_for(None) == []

    unrelated = whisper_service.build_whisper_initial_prompt(course_name="英语口语-001")
    assert "Spring" not in unrelated
    assert unrelated == "这是一门中文技术课程：《英语口语-001》。"


# ---------- Test 4：去重 ----------


def test_terms_are_deduplicated():
    """Test 4：["Spring","Java","Spring"] 里的 Spring 只能出现一次。

    重复项会白占 decoder 上下文（get_prompt 只保留 223 token），
    也会让术语表看起来像在刷权重。
    """
    prompt = whisper_service.build_whisper_initial_prompt(terms=["Spring", "Java", "Spring"])
    assert prompt.count("Spring") == 1
    assert prompt.count("Java") == 1


def test_terms_dedupe_is_case_insensitive_and_order_stable():
    """Test 4 边界：大小写不同算同一个词，保留**首次出现**的写法；顺序稳定。"""
    prompt = whisper_service.build_whisper_initial_prompt(terms=["Spring", "spring", "SPRING"])
    assert prompt.count("Spring") == 1
    assert "spring" not in prompt.replace("Spring", "")
    first = whisper_service.build_whisper_initial_prompt(terms=["MyBatis", "Java"])
    second = whisper_service.build_whisper_initial_prompt(terms=["MyBatis", "Java"])
    assert first == second, "同样的输入必须得到同样的 prompt（顺序稳定）"


# ---------- token 预算保护（用户拍板：≤ 210~215，先裁 tail 再裁低优先级术语）----------


def test_default_prompt_fits_budget_without_dropping_terms():
    """默认 23 词 prompt 应当**完整**进预算（不该在正常配置下触发裁剪）。"""
    prompt = whisper_service.build_whisper_initial_prompt(course_name="001.AI版SSM教程简介")
    assert whisper_service._estimate_tokens(prompt) <= whisper_service.TOKEN_BUDGET
    assert "尚硅谷" in prompt, "预算够时不该裁掉任何术语（含最低优先级的品牌名）"


def test_budget_trims_low_priority_terms_first(monkeypatch):
    """预算不足时**从表尾裁**：核心术语必须活下来，扩展词 / 品牌名先牺牲。

    词表顺序即优先级，所以这条守的是"词表顺序"这个隐性契约 ——
    谁把 Java 挪到表尾，这条就会红。
    """
    monkeypatch.setattr(whisper_service, "TOKEN_BUDGET", 100)   # 只够放 11 个词
    prompt = whisper_service.build_whisper_initial_prompt(course_name="001.AI版SSM教程简介")
    assert whisper_service._estimate_tokens(prompt) <= 100
    for core in ("Java", "SSM", "Spring", "MyBatis"):
        assert core in prompt, f"核心术语 {core} 被裁掉了 —— 裁剪必须从低优先级开始"
    assert "尚硅谷" not in prompt, "品牌名优先级最低，应当最先被裁"


def test_budget_protection_prefers_terms_over_tail(monkeypatch):
    """术语优先于尾部上下文：预算只剩一点时，宁可丢 tail 也不能丢术语。"""
    course_prompt = whisper_service.build_whisper_initial_prompt(course_name="001.AI版SSM教程简介")
    monkeypatch.setattr(whisper_service, "TOKEN_BUDGET", 170)   # 术语表约 158，只剩 ~12
    composed = whisper_service._compose_slice_prompt(course_prompt, "上" * 200)
    assert course_prompt in composed
    assert whisper_service._estimate_tokens(composed) <= 170
    assert len(composed) < len(course_prompt) + 20, "tail 必须被大幅裁剪"


def test_estimate_tokens_is_conservative_for_cjk():
    """估算对 CJK 取 1 token/字（实测 0.92）—— 宁高勿低，否则保护会失效。"""
    assert whisper_service._estimate_tokens("你好世界") == 4
    assert whisper_service._estimate_tokens("Spring") == 4       # int(6 * 0.7)
    assert whisper_service._estimate_tokens("") == 0


# ---------- Test 5：空术语兼容 ----------


def test_empty_terms_still_transcribes(monkeypatch, tmp_path):
    """Test 5：没有任何术语时照常转写，不抛异常，prompt 仍是合法字符串。"""
    monkeypatch.setattr(
        whisper_service, "build_whisper_initial_prompt", lambda *a, **kw: "这是一门中文课程。"
    )
    recorded = []
    kwargs = _run_once(monkeypatch, tmp_path, recorded)
    assert kwargs["initial_prompt"] == "这是一门中文课程。"
    assert kwargs["language"] == "zh"


def test_build_prompt_without_anything_returns_fallback():
    """Test 5 单元级：无课程名 + 无术语 → 兜底句，不是空串。"""
    prompt = whisper_service.build_whisper_initial_prompt(course_name=None, terms=[])
    assert prompt == "这是一门中文课程。"


# ---------- Test 6：状态机不受影响 ----------


def test_state_machine_unchanged_with_prompt(client, db_session, monkeypatch, tmp_path):  # noqa: F811
    """Test 6：加了 prompt 之后，生成成功仍然是 ready + unreviewed + whisper。

    Prompt 只能提升识别准确率，不改变任务生命周期：排队和生成阶段保留旧字幕
    的校对状态；只有新字幕成功写回后才重置为 unreviewed。
    """
    monkeypatch.setattr(whisper_service, "is_ffmpeg_available", lambda: True)
    monkeypatch.setattr(whisper_service, "_start_worker", lambda: None)
    monkeypatch.setattr(whisper_service, "SessionLocal", lambda: db_session)

    course_dir = tmp_path / "c-prompt"
    course_dir.mkdir(parents=True, exist_ok=True)
    video = course_dir / "v.mp4"
    video.write_bytes(b"\x00" * 16)
    db_session.add(
        Material(
            course_id="c-prompt",
            dir_path=str(course_dir),
            status="ready",
            video_path=str(video),
            subtitle_status="pending",
            review_state="reviewed",   # 故意：证明入队时保留、成功后才重置
        )
    )
    db_session.commit()

    resp = client.post(
        "/api/admin/materials/c-prompt/generate-subtitle", headers=_h()
    )
    assert resp.status_code == 200, resp.text

    material = db_session.query(Material).filter(Material.course_id == "c-prompt").one()
    assert material.subtitle_status == "generating"
    assert material.review_state == "reviewed"
    assert material.subtitle_source is None

    # 模拟 worker 跑完写回（真实链路里由 _worker_loop 调用）
    whisper_service._write_back_to_db(
        course_id="c-prompt", outcome="success", vtt_path=str(course_dir / "v.whisper.vtt")
    )
    db_session.expire_all()
    material = db_session.query(Material).filter(Material.course_id == "c-prompt").one()
    assert material.subtitle_status == "ready"
    assert material.subtitle_source == "whisper"
    assert material.review_state == "unreviewed"
    assert material.subtitle_error is None
