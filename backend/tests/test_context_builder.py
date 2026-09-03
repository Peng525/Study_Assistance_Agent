"""模块 3.2 context_builder 测试（核心，重点覆盖）。"""

from app.services.context_builder import (
    build_context,
    estimate_tokens,
    extract_time_window,
    filter_courseware,
    parse_vtt_cues,
    split_chapters,
)

VTT = """WEBVTT

00:00:10.000 --> 00:00:15.000
第一段

00:01:00.000 --> 00:01:30.000
第二段

00:03:00.000 --> 00:03:10.000
第三段

00:06:00.000 --> 00:06:20.000
第四段
"""


def test_parse_vtt_cues():
    cues = parse_vtt_cues(VTT)
    assert len(cues) == 4
    assert cues[0]["text"] == "第一段"
    assert cues[0]["start"] == 10.0


def test_extract_time_window():
    cues = parse_vtt_cues(VTT)
    # 选中 300s（5min）→ ±3min = 120s~480s，取第三段(180s)+第四段(360s)
    text = extract_time_window(cues, start_time=300.0)
    assert "第三段" in text
    assert "第四段" in text
    assert "第一段" not in text  # 10s 超出窗口
    assert "第二段" not in text  # 60s 超出窗口


def test_split_chapters():
    md = "# 第一章\n内容A\n# 第二章\n内容B\n# 第三章\n内容C"
    chapters = split_chapters(md)
    assert len(chapters) == 3
    assert "内容A" in chapters[0]


def test_filter_courseware_with_chapters():
    md = "# 1\nA\n# 2\nB\n# 3\nC\n# 4\nD\n# 5\nE"
    # 无时间戳 → 无法定位章节，回退全文（PRD 7.1）
    filtered = filter_courseware(md, has_chapters=True, max_chapters=3)
    assert "A" in filtered
    assert "E" in filtered


def test_filter_courseware_map_by_time():
    md = "# 1\nA\n# 2\nB\n# 3\nC\n# 4\nD\n# 5\nE"
    # 选中 10s，总时长 100s → 比例 0.1 → 映射到第 0 章（第一章无前章，取第1~2章）
    filtered = filter_courseware(md, has_chapters=True, start_time=10, video_duration=100)
    assert "A" in filtered  # 第1章
    assert "B" in filtered  # 第2章
    assert "C" not in filtered  # 第3章及之后不在窗口
    assert "D" not in filtered
    assert "E" not in filtered


def test_filter_courseware_map_by_time_middle():
    md = "# 1\nA\n# 2\nB\n# 3\nC\n# 4\nD\n# 5\nE"
    # 选中 50s，总时长 100s → 比例 0.5 → 映射到第 2 章（第1~3章，即 B/C/D）
    filtered = filter_courseware(md, has_chapters=True, start_time=50, video_duration=100)
    assert "B" in filtered
    assert "C" in filtered
    assert "D" in filtered
    assert "A" not in filtered
    assert "E" not in filtered


def test_filter_courseware_no_chapters_full_text():
    text = "纯文本无标题"
    assert filter_courseware(text, has_chapters=False) == text


def test_build_context_basic():
    messages, notice = build_context(
        courseware_text="课件内容",
        transcript="逐字稿",
        selected_subtitle="选中的字幕",
        question="这是问题",
    )
    assert notice == ""
    assert messages[0]["role"] == "system"
    assert "简洁书面中文" in messages[0]["content"]
    assert "Markdown 装饰符" in messages[0]["content"]
    assert "不是回答内容的上限" in messages[0]["content"]
    assert "严格定义与边界" in messages[0]["content"]
    assert "优点与局限" in messages[0]["content"]
    assert "适用与不适用场景" in messages[0]["content"]
    assert "通俗语言或贴切类比" in messages[0]["content"]
    assert messages[-1]["role"] == "user"
    assert "课件内容" in messages[-1]["content"]
    assert "这是问题" in messages[-1]["content"]


def test_build_context_history_limited():
    history = []
    for i in range(10):
        history.append({"role": "user", "content": f"q{i}"})
        history.append({"role": "assistant", "content": f"a{i}"})
    messages, _ = build_context(question="新问题", history=history)
    # 模型保留最近 10 轮 = 20 条，完整历史由数据库另行保存。
    history_msgs = messages[1:-1]
    assert len(history_msgs) == 20


def test_build_context_token_reject():
    # 构造超长课件触发 >32K 拒绝
    huge = "课" * 40_000
    messages, notice = build_context(courseware_text=huge, question="q")
    assert messages == []
    assert "超过模型上下文限制" in notice


def test_build_context_token_truncate():
    # 构造 29K 内容触发截断到 5 轮
    huge = "课" * 29_000
    history = []
    for i in range(6):
        history.append({"role": "user", "content": "q"})
        history.append({"role": "assistant", "content": "a"})
    messages, notice = build_context(courseware_text=huge, question="q", history=history)
    assert "最近 5 轮" in notice
    assert len(messages) >= 3


def test_build_context_includes_long_term_memory():
    messages, notice = build_context(
        question="继续",
        memory_summary="用户正在学习 Spring，尚未解决循环依赖问题。",
    )
    assert notice == ""
    assert "【专栏长期对话记忆】" in messages[-1]["content"]
    assert "循环依赖" in messages[-1]["content"]


def test_estimate_tokens():
    assert estimate_tokens("你好世界") == 4
