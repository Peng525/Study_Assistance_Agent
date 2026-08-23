"""context_builder：构造发给大模型的 messages（PRD 第 7 章核心）。

策略：
1. 系统 prompt（助学者模板）
2. 课件粗筛：有章节取前 3 章（Phase 0 简化，无 RAG 无法精确映射时间戳→章节）
3. 逐字稿时间窗：选中字幕 ±3 分钟
4. 多轮历史：最近 5 轮（10 条），超出丢最旧
5. 当前提问
6. Token 预检：28K/30K/32K 阈值截断/拒绝
"""

SYSTEM_PROMPT = (
    "你是 AI 学习搭档，一位耐心的助学助手。"
    "你会结合用户提供的课件内容和视频逐字稿，解答用户在学习视频时遇到的问题。"
    "回答要准确、简洁、有结构，优先引用上下文中的知识点。"
    "如果上下文不足以回答，请如实说明，不要编造。"
)

TIME_WINDOW_SECONDS = 180  # ±3 分钟
MAX_HISTORY_ROUNDS = 5  # 最近 5 轮 = 10 条 messages

# Token 阈值（PRD 第 7.2 节）
TOKEN_TRUNCATE_3 = 28_000  # >28K 截断到 3 轮
TOKEN_TRUNCATE_1 = 30_000  # >30K 截断到 1 轮
TOKEN_REJECT = 32_000  # >32K 拒绝


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文 1 字 ≈ 1 token）。"""
    return len(text)


def parse_vtt_cues(vtt_text: str) -> list[dict]:
    """解析 VTT 文本，返回 [{start, end, text}]。"""
    cues = []
    lines = vtt_text.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if "-->" in line:
            start_str, end_str = line.split("-->")[0].strip(), line.split("-->")[1].strip()
            start = _ts_to_seconds(start_str)
            end = _ts_to_seconds(end_str)
            i += 1
            text_lines = []
            while i < n and lines[i].strip() != "":
                text_lines.append(lines[i].strip())
                i += 1
            cues.append({"start": start, "end": end, "text": "\n".join(text_lines)})
            continue
        i += 1
    return cues


def _ts_to_seconds(ts: str) -> float:
    """'HH:MM:SS.mmm' 或 'MM:SS.mmm' → 秒。"""
    parts = ts.replace(",", ".").split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    return float(parts[0])


def extract_time_window(cues: list[dict], start_time: float, window: int = TIME_WINDOW_SECONDS) -> str:
    """取选中时间戳 ±window 秒内的字幕文本。"""
    lo = start_time - window
    hi = start_time + window
    selected = [c["text"] for c in cues if lo <= c["start"] <= hi]
    return "\n".join(selected)


def split_chapters(text: str) -> list[str]:
    """按标题行分割章节（# 开头）。无标题则返回全文单章。"""
    lines = text.split("\n")
    chapters = []
    current: list[str] = []
    for line in lines:
        if line.strip().startswith("#"):
            if current:
                chapters.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        chapters.append("\n".join(current))
    return chapters if chapters else [text]


def filter_courseware(
    text: str,
    has_chapters: bool,
    max_chapters: int = 3,
    *,
    start_time: float | None = None,
    video_duration: float | None = None,
) -> str:
    """课件粗筛：取选中字幕所在章节 ±1 章（共 3 章）。

    - 无章节：全文
    - 有章节但无法定位（缺时间戳/时长）：回退全文
    - 有章节且能定位：按 `start_time / video_duration` 比例映射到章节，
      取该章节及前后各 1 章
    """
    if not has_chapters:
        return text
    chapters = split_chapters(text)
    if len(chapters) <= max_chapters:
        return text

    # 无法定位选中字幕所属章节 → 回退全文（PRD 7.1）
    if start_time is None or not video_duration or video_duration <= 0:
        return text

    ratio = start_time / video_duration
    center = int(ratio * len(chapters))
    center = max(0, min(len(chapters) - 1, center))
    lo = max(0, center - 1)
    hi = min(len(chapters), center + 2)  # 共 3 章：center-1, center, center+1
    return "\n\n".join(chapters[lo:hi])


def _build_base_messages(courseware_text: str, transcript: str, selected: str, question: str) -> list[dict]:
    """构造基础 messages（不含历史）。"""
    system = SYSTEM_PROMPT
    context_parts = []
    if courseware_text:
        context_parts.append(f"【课件内容】\n{courseware_text}")
    if transcript:
        context_parts.append(f"【视频逐字稿（选中时间点±3分钟）】\n{transcript}")
    context_block = "\n\n".join(context_parts)
    user_msg = f"{context_block}\n\n【用户选中的字幕】{selected}\n\n【用户疑问】{question}" if context_block else question
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]


def build_context(
    *,
    courseware_text: str = "",
    courseware_has_chapters: bool = False,
    transcript: str = "",
    selected_subtitle: str = "",
    question: str = "",
    history: list[dict] | None = None,
    start_time: float | None = None,
    video_duration: float | None = None,
) -> tuple[list[dict], str]:
    """构造完整 messages。返回 (messages, 提示信息)。

    history: 最近 N 轮的 [{role, content}]（不含 system）。
    提示信息：空串表示正常，否则为给用户看的降级提示。
    """
    # 课件粗筛（按选中字幕时间戳映射章节）
    courseware = filter_courseware(
        courseware_text,
        courseware_has_chapters,
        start_time=start_time,
        video_duration=video_duration,
    )

    base = _build_base_messages(courseware, transcript, selected_subtitle, question)

    # 历史只保留最近 5 轮（10 条）
    history = (history or [])[-MAX_HISTORY_ROUNDS * 2 :]

    messages = [base[0]] + list(history) + [base[1]]
    total = sum(estimate_tokens(m["content"]) for m in messages)

    notice = ""
    # Token 预检（PRD 阈值：>28K 截3轮，>30K 截1轮，>32K 拒绝）
    if total > TOKEN_REJECT:
        # 尝试截断到 1 轮
        messages = [base[0]] + list(history[-2:]) + [base[1]]
        total = sum(estimate_tokens(m["content"]) for m in messages)
        if total > TOKEN_REJECT:
            return [], "上下文超限，请清空会话或选择大上下文模型"
        notice = "上下文过长，已精简历史到最近 1 轮"
    elif total > TOKEN_TRUNCATE_1:
        messages = [base[0]] + list(history[-2:]) + [base[1]]
        notice = "上下文过长，已精简历史到最近 1 轮"
    elif total > TOKEN_TRUNCATE_3:
        messages = [base[0]] + list(history[-6:]) + [base[1]]
        notice = "上下文过长，已精简历史到最近 3 轮"

    return messages, notice
