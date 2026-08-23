"""模块 2.3 srt→vtt 转换测试。"""

from app.services.subtitle import detect_unsupported_format, srt_to_vtt


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
