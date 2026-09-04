"""模块 2.3 srt→vtt 转换测试。

A2 追加：cues_to_vtt / cue_revision / fmt_vtt_ts（与 parse_vtt_cues 互逆）。
"""

from app.services.context_builder import _ts_to_seconds, parse_vtt_cues
from app.services.subtitle import cue_revision, cues_to_vtt, detect_unsupported_format, fmt_vtt_ts, srt_to_vtt


def test_srt_to_vtt_basic():
    srt = "1\n00:00:15,000 --> 00:00:18,000\n这就是一条字幕文本\n\n2\n00:00:18,000 --> 00:00:20,500\n第二条字幕\n"
    vtt = srt_to_vtt(srt)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:15.000 --> 00:00:18.000" in vtt
    assert "这就是一条字幕文本" in vtt
    assert "第二条字幕" in vtt
    # 序号行被去掉
    assert "\n1\n" not in vtt


def test_srt_to_vtt_strips_style_tags():
    srt = "1\n00:00:01,000 --> 00:00:02,000\n<i>斜体</i>和<c.red>红色</c>\n"
    vtt = srt_to_vtt(srt)
    assert "<i>" not in vtt
    assert "<c.red>" not in vtt
    assert "斜体和红色" in vtt


def test_srt_to_vtt_multiline_text():
    srt = "1\n00:00:01,000 --> 00:00:03,000\n第一行\n第二行\n"
    vtt = srt_to_vtt(srt)
    assert "第一行\n第二行" in vtt


def test_detect_ass():
    assert detect_unsupported_format("[Script Info]\nTitle: x") == "暂不支持 ASS/SSA 字幕，请转换为 srt 格式"


def test_detect_bilibili_json():
    assert "B 站" in detect_unsupported_format('{"body": [{"content": "x"}]}')


def test_detect_supported_vtt():
    assert detect_unsupported_format("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\ntext") is None


# --------------------------------------------------------------------------
# A2：cues_to_vtt / cue_revision / fmt_vtt_ts
# --------------------------------------------------------------------------


def test_cues_to_vtt_basic():
    vtt = cues_to_vtt(
        [
            {"start": 1.0, "end": 2.5, "text": "第一条"},
            {"start": 3.0, "end": 4.0, "text": "第二条"},
        ]
    )
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:01.000 --> 00:00:02.500" in vtt
    assert "00:00:03.000 --> 00:00:04.000" in vtt
    assert "第一条" in vtt and "第二条" in vtt


def test_cues_to_vtt_empty_returns_valid_header():
    """空列表必须返回合法空 VTT，不能是空字符串（否则 parse 出 0 条且文件非法）。"""
    assert cues_to_vtt([]) == "WEBVTT\n"
    assert cues_to_vtt(None) == "WEBVTT\n"


def test_cues_to_vtt_sorts_by_start():
    """输入乱序时按 start 升序输出。"""
    vtt = cues_to_vtt(
        [
            {"start": 9.0, "end": 10.0, "text": "后"},
            {"start": 1.0, "end": 2.0, "text": "先"},
        ]
    )
    assert vtt.index("先") < vtt.index("后")


def test_cues_to_vtt_does_not_mutate_input():
    cues = [{"start": 5.0, "end": 6.0, "text": "b"}, {"start": 1.0, "end": 2.0, "text": "a"}]
    original = [dict(c) for c in cues]
    cues_to_vtt(cues)
    assert cues == original


def test_roundtrip_cues_to_vtt_to_cues():
    """核心契约：cues → vtt → parse_vtt_cues 应还原出等价的 cues。"""
    source = [
        {"start": 0.0, "end": 2.0, "text": "你好世界"},
        {"start": 2.5, "end": 5.25, "text": "这是第二条"},
        {"start": 3661.5, "end": 3663.0, "text": "一小时以后"},
    ]
    parsed = parse_vtt_cues(cues_to_vtt(source))
    assert len(parsed) == 3
    for before, after in zip(source, parsed):
        assert abs(after["start"] - before["start"]) < 0.001
        assert abs(after["end"] - before["end"]) < 0.001
        assert after["text"] == before["text"]


def test_roundtrip_preserves_multiline_text():
    """多行文本要原样保留（parse 用 \\n join）。"""
    cues = [{"start": 0.0, "end": 2.0, "text": "第一行\n第二行"}]
    parsed = parse_vtt_cues(cues_to_vtt(cues))
    assert parsed[0]["text"] == "第一行\n第二行"


def test_cue_text_blank_lines_are_stripped():
    """正文里的空行会让 parse_vtt_cues 提前结束该条 —— 必须被清洗掉。"""
    cues = [{"start": 0.0, "end": 2.0, "text": "前段\n\n后段"}]
    vtt = cues_to_vtt(cues)
    parsed = parse_vtt_cues(vtt)
    assert len(parsed) == 1
    assert parsed[0]["text"] == "前段\n后段"


def test_cue_text_arrow_is_escaped():
    """正文含 '-->' 会被 parse_vtt_cues 误判为时间戳行 —— 必须转义。"""
    cues = [{"start": 0.0, "end": 2.0, "text": "流程 A --> B 结束"}]
    vtt = cues_to_vtt(cues)
    parsed = parse_vtt_cues(vtt)
    assert len(parsed) == 1, "转义后仍应只有一条 cue"
    assert "-->" not in parsed[0]["text"]


def test_cue_with_empty_text_is_skipped():
    cues = [
        {"start": 0.0, "end": 2.0, "text": "有效"},
        {"start": 2.0, "end": 3.0, "text": "   "},
        {"start": 3.0, "end": 4.0, "text": None},
    ]
    parsed = parse_vtt_cues(cues_to_vtt(cues))
    assert len(parsed) == 1
    assert parsed[0]["text"] == "有效"


def test_fmt_vtt_ts_formats_and_clamps():
    assert fmt_vtt_ts(0) == "00:00:00.000"
    assert fmt_vtt_ts(1.5) == "00:00:01.500"
    assert fmt_vtt_ts(3661.75) == "01:01:01.750"
    # 非法 / 边界输入不能产出坏时间戳
    assert fmt_vtt_ts(None) == "00:00:00.000"
    assert fmt_vtt_ts("abc") == "00:00:00.000"
    assert fmt_vtt_ts(-5) == "00:00:00.000"
    assert fmt_vtt_ts(float("nan")) == "00:00:00.000"
    assert fmt_vtt_ts(float("inf")) == "00:00:00.000"


def test_fmt_vtt_ts_roundtrip_with_ts_to_seconds():
    for sec in (0.0, 1.007, 59.999, 3600.0, 7325.5):
        assert abs(_ts_to_seconds(fmt_vtt_ts(sec)) - sec) < 0.001


def test_cue_revision_is_stable_and_content_sensitive():
    vtt = cues_to_vtt([{"start": 0.0, "end": 1.0, "text": "内容"}])
    assert cue_revision(vtt) == cue_revision(vtt)
    assert len(cue_revision(vtt)) == 8
    # 内容一变，指纹必须变（乐观锁靠它检测并发编辑）
    changed = cues_to_vtt([{"start": 0.0, "end": 1.0, "text": "内容变了"}])
    assert cue_revision(vtt) != cue_revision(changed)
    # 时间轴变化同样要能被检出
    moved = cues_to_vtt([{"start": 0.5, "end": 1.0, "text": "内容"}])
    assert cue_revision(vtt) != cue_revision(moved)
    assert cue_revision("") == cue_revision(None)
