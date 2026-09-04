"""字幕格式处理：srt → vtt 转换、cues ↔ vtt 序列化、样式标签清洗。

cues 的统一形态（与 `context_builder.parse_vtt_cues` 完全一致）：
    [{"start": float 秒, "end": float 秒, "text": str}, ...]

A2 新增 `cues_to_vtt` / `cue_revision`。序列化与业务校验分层：
    本模块只保证「写出来的文件结构安全」，不做业务校验；
    业务校验（时间轴逆序、start>=end 等）由调用方的 sanitize_cues 负责（A4）。
"""

import hashlib
import re

from app.models.models import (
    SUBTITLE_REVIEW_REVIEWED,
    SUBTITLE_STATUS_READY,
)

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


# --------------------------------------------------------------------------
# A2：cues → vtt 序列化 + 版本指纹
# --------------------------------------------------------------------------


def _as_float(value, default: float = 0.0) -> float:
    """转 float 并挡掉 None / NaN / inf / 负数（时间轴不允许负值）。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return max(0.0, f)


def fmt_vtt_ts(seconds) -> str:
    """秒 → 'HH:MM:SS.mmm'（与 context_builder._ts_to_seconds 互逆）。"""
    total_ms = int(round(_as_float(seconds) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _sanitize_cue_text(text) -> str:
    """清洗单条字幕文本，保证写盘后不会破坏 VTT 结构。

    parse_vtt_cues 有两个脆弱点，这里逐一防住：
      1. 遇到空行就认为该条 cue 结束 → 内部空行会截断字幕
      2. 用 `"-->" in line` 判定时间戳行 → 正文含 --> 会被当成新 cue 的开头
    """
    if text is None:
        return ""
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in normalized.split("\n")]
    lines = [ln for ln in lines if ln]  # 去空行，防止解析截断
    # WebVTT 规范：正文里的 "-->" 转义为 "-\->"
    lines = [ln.replace("-->", r"-\->") for ln in lines]
    return "\n".join(lines)


def cues_to_vtt(cues: list[dict]) -> str:
    """[{start, end, text}] → WebVTT 文本。

    - 按 start 升序排列（不改动原列表）
    - 时间统一格式化为 HH:MM:SS.mmm
    - 文本做结构安全清洗（去空行、转义 -->）
    - 空列表返回合法的空 VTT（"WEBVTT\\n"），不返回空字符串
    """
    lines = ["WEBVTT", ""]

    ordered = sorted(cues or [], key=lambda c: _as_float(c.get("start")))
    for cue in ordered:
        text = _sanitize_cue_text(cue.get("text"))
        if not text:
            continue  # 空文本没有意义，且会让 parse 得到一个空 cue
        start_ts = fmt_vtt_ts(cue.get("start"))
        end_ts = fmt_vtt_ts(cue.get("end"))
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def cue_revision(vtt_text: str) -> str:
    """VTT 内容指纹（sha1 前 8 位），用于编辑时的乐观锁。

    用内容哈希而非文件 mtime：mtime 精度有限，连续保存可能撞值；
    内容只要变了哈希就一定不同，且不需要额外字段。
    """
    return hashlib.sha1((vtt_text or "").encode("utf-8")).hexdigest()[:8]


def validate_cues(cues: list) -> str | None:
    """时间轴合法性校验（前后端都要做，非法时间轴会让播放器崩溃）。

    返回 None 表示合法；否则返回人类可读的错误文案。
    规则：每条 cue 有数字 start/end；start>=0；end>start；时间戳有限。
    """
    if not isinstance(cues, list):
        return "cues 必须是数组"
    for idx, c in enumerate(cues):
        if not isinstance(c, dict):
            return f"第 {idx + 1} 条不是对象"
        try:
            s = float(c.get("start"))
            e = float(c.get("end"))
        except (TypeError, ValueError):
            return f"第 {idx + 1} 条 start/end 不是数字"
        if s != s or e != e or s in (float("inf"), float("-inf")) or e in (float("inf"), float("-inf")):
            return f"第 {idx + 1} 条时间戳非法（NaN/inf）"
        if s < 0:
            return f"第 {idx + 1} 条开始时间不能为负"
        if e <= s:
            return f"第 {idx + 1} 条结束时间必须大于开始时间"
    return None


def transcript_context_allowed(subtitle_status: str | None, review_state: str | None) -> bool:
    """是否允许把字幕**自动**注入为 Transcript Context（±180 秒时间窗）。

    三件事必须分开判断，不要混成一个状态：
        subtitle_status  字幕**有没有生成好**（pending/generating/ready/error）
        review_state     字幕**能不能作为自动 AI 证据**（unreviewed/reviewed）
        CC 开关（前端）  用户屏幕上看不看得到

    门控规则：只有 ready + reviewed 才解锁自动注入。

    ⚠️ 措辞纪律：不要说「生成即生效」。生成完成的准确含义是
       「允许展示（CC 可见）+ 允许被用户主动引用（Selected Evidence）」，
       **未审核前不得自动作为 Transcript Context** —— 未校对的转写混进
       上下文会直接污染答案，而这是本项目最核心的卖点。
    """
    return subtitle_status == SUBTITLE_STATUS_READY and review_state == SUBTITLE_REVIEW_REVIEWED
