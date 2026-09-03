"""context_builder：构造发给大模型的 messages（PRD 第 7 章核心）。

策略：
1. 系统 prompt（助学者模板）
2. 课件粗筛：有章节取前 3 章（Phase 0 简化，无 RAG 无法精确映射时间戳→章节）
3. 逐字稿时间窗：选中字幕 ±3 分钟
4. 多轮历史：最近 10 轮（20 条），更早内容由长期记忆摘要承接
5. 当前提问
6. Token 预检：28K/30K/32K 阈值截断/拒绝
"""

SYSTEM_PROMPT = (
    "你是 AI 学习搭档，一位耐心的助学助手。请在内部判断问题属于项目问题、混合问题还是知识拓展，"
    "不要向用户展示分类标签。项目问题必须优先依据项目摘要、项目原始证据、当前视频资料和已审核逐字稿；"
    "专栏总大纲与当前视频课件原文冲突时，以课件原文为准并指出大纲可能需要更新。"
    "项目资料没有规定的事实必须明确说明，不得把通用建议伪装成项目现状；可以补充通用知识，"
    "但项目相关建议最终要回到当前项目。纯知识拓展可以直接解释通用知识。"
    "课件用于提供当前课程语境、项目事实和案例线索，不是回答内容的上限，也不要把回答写成课件原文复述。"
    "遇到理论、概念或原理类问题时，应按问题复杂度讲清其产生背景与要解决的问题、严格定义与边界、"
    "内部机制或逻辑链、优点与局限、适用与不适用场景，并结合当前课件或项目给出实际案例；"
    "专业解释之后，再用通俗语言或贴切类比重新解释一次。只有比较、流程或关系确实更清楚时才使用"
    "简洁表格或文本流程图，不要为了形式强行制图。"
    "系统未提供联网搜索能力，不得声称已经搜索互联网。"
    "播放时间仅是定位锚点；没有逐字稿或时间轴证据时，不得声称知道该时间点具体声音或画面。"
    "回答要准确、简洁、有结构；使用简洁书面中文、短标题和自然段，避免输出多余的 Markdown 装饰符；"
    "上下文不足时如实说明，不要编造。"
)

TIME_WINDOW_SECONDS = 180  # ±3 分钟
MAX_HISTORY_ROUNDS = 10

# Token 阈值（PRD 第 7.2 节）
TOKEN_TRUNCATE_5 = 28_000
TOKEN_TRUNCATE_2 = 30_000
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


def _build_base_messages(
    courseware_text: str,
    transcript: str,
    selected: str,
    question: str,
    *,
    project_summary: str = "",
    project_evidence: str = "",
    column_outline: str = "",
    memory_summary: str = "",
    video_context: str = "",
) -> list[dict]:
    """构造基础 messages（不含历史）。"""
    system = SYSTEM_PROMPT
    context_parts = []
    if project_summary:
        context_parts.append(f"【已审核项目背景摘要】\n{project_summary}")
    if video_context:
        context_parts.append(f"【当前视频元数据】\n{video_context}")
    if column_outline:
        context_parts.append(f"【专栏总大纲】\n{column_outline}")
    if memory_summary:
        context_parts.append(f"【专栏长期对话记忆】\n{memory_summary}")
    if courseware_text:
        context_parts.append(f"【当前视频课件原文】\n{courseware_text}")
    if project_evidence:
        context_parts.append(f"【项目原始证据】\n{project_evidence}")
    if transcript:
        context_parts.append(f"【已审核视频逐字稿（当前时间点±3分钟）】\n{transcript}")
    context_block = "\n\n".join(context_parts)
    selected_block = f"\n\n【用户选中的字幕】{selected}" if selected else ""
    user_msg = (
        f"{context_block}{selected_block}\n\n【用户疑问】{question}" if context_block else question
    )
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
    project_summary: str = "",
    project_evidence: str = "",
    column_outline: str = "",
    memory_summary: str = "",
    video_context: str = "",
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

    base = _build_base_messages(
        courseware,
        transcript,
        selected_subtitle,
        question,
        project_summary=project_summary,
        project_evidence=project_evidence,
        column_outline=column_outline,
        memory_summary=memory_summary,
        video_context=video_context,
    )

    # 历史只保留模型需要的 role/content，内部调度元数据不得透传给供应商。
    history = [
        {"role": item["role"], "content": item.get("content", "")}
        for item in (history or [])
        if item.get("role") in {"user", "assistant"}
    ][-MAX_HISTORY_ROUNDS * 2 :]

    messages = [base[0]] + list(history) + [base[1]]
    total = sum(estimate_tokens(m["content"]) for m in messages)

    notice = ""
    # 优先保留专栏事实与当前视频课件，历史按 10 → 5 → 2 轮缩减。
    if total > TOKEN_REJECT:
        messages = [base[0]] + list(history[-4:]) + [base[1]]
        total = sum(estimate_tokens(m["content"]) for m in messages)
        if total > TOKEN_REJECT:
            return [], "当前专栏资料与视频课件超过模型上下文限制，请联系管理员精简资料"
        notice = "上下文过长，本轮模型仅携带最近 2 轮对话；完整历史仍已保存"
    elif total > TOKEN_TRUNCATE_2:
        messages = [base[0]] + list(history[-4:]) + [base[1]]
        notice = "上下文过长，本轮模型仅携带最近 2 轮对话；完整历史仍已保存"
    elif total > TOKEN_TRUNCATE_5:
        messages = [base[0]] + list(history[-10:]) + [base[1]]
        notice = "上下文过长，本轮模型仅携带最近 5 轮对话；完整历史仍已保存"

    return messages, notice
