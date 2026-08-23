"""字幕格式处理：srt → vtt 转换 + 样式标签清洗。"""

import re

# 需要清洗的 WebVTT 内联样式标签
_STYLE_TAG_RE = re.compile(r"</?(c|i|b|u|ruby|rt|v|lang)[^>]*>", re.IGNORECASE)

# 非标字幕格式检测
def detect_unsupported_format(content: str) -> str | None:
    """检测是否为不支持的格式，返回拒绝文案；支持则返回 None。"""
    stripped = content.lstrip()
    if stripped.startswith("[Script Info]") or "[Events]" in content[:2000]:
        return "暂不支持 ASS/SSA 字幕，请转换为 srt 格式"
    if stripped.startswith("{") and '"body"' in content[:2000]:
        return "暂不支持 B 站 CC JSON 字幕，请用字幕下载工具导出 srt/vtt"
    return None


def _strip_style_tags(text: str) -> str:
    return _STYLE_TAG_RE.sub("", text)


def srt_to_vtt(srt_content: str) -> str:
    """将 SRT 文本转换为标准 VTT 文本。

    - 首行加 WEBVTT
    - 时间戳逗号 `,` 改点 `.`
    - 去掉序号行
    - 清洗内联样式标签
    """
    lines = srt_content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    vtt_lines = ["WEBVTT", ""]

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        # 跳过空行
        if not line:
            i += 1
            continue
        # 时间戳行（含 --> 且带逗号毫秒）
        if "-->" in line:
            timestamp = line.replace(",", ".")
            vtt_lines.append(timestamp)
            i += 1
            # 收集后续文本行直到空行
            text_lines = []
            while i < n and lines[i].strip() != "":
                text_lines.append(_strip_style_tags(lines[i].strip()))
                i += 1
            vtt_lines.extend(text_lines)
            vtt_lines.append("")  # 每条字幕后空行
            continue
        # 否则是序号行或未知行，跳过
        i += 1

    return "\n".join(vtt_lines).rstrip() + "\n"
