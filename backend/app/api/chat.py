"""AI 对话接口（SSE 流式 + 会话管理）。"""

import json
import uuid
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.admin_model_configs import get_default_config
from app.core.database import get_db
from app.core.security import decrypt_api_key
from app.models.models import (
    ChatMessage,
    ChatContextBinding,
    ChatSession,
    ColumnChatSession,
    Material,
    ModelConfig,
    ProjectSource,
    ProjectSourceOutline,
    User,
    VideoKnowledge,
)
from app.services.context_builder import build_context, parse_vtt_cues, extract_time_window
from app.services.subtitle import transcript_context_allowed
from app.services.llm_client import stream_chat
from app.services.llm_audit import create_call_log, update_call_log
from app.services.model_router import RoutingOutcome, stream_model_chain
from app.services.project_context import (
    get_project_for_material,
    latest_published_version,
    version_context,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
SESSION_WRITE_RETRIES = 5
RECENT_COLUMN_MESSAGES = 20
SUMMARY_BATCH_MESSAGES = 10


class ChatRequest(BaseModel):
    course_id: str | None = None
    selected_subtitle: str = ""
    # start/end = Active Citation 的**字幕区间**（用户选中的那一条）
    start_time: float | None = None
    end_time: float | None = None
    # A3：用户**当前播放位置**，与 start/end 语义分离。
    # ±180 秒 Transcript Context 的时间窗基准用它；缺省时回退到 start_time，
    # 保证老客户端（未升级的前端）行为不变。
    current_time: float | None = None
    video_duration: float | None = Field(default=None, ge=0)
    user_question: str = Field(min_length=1)
    session_id: str | None = None


def _create_chat_audit(
    bind,
    current: User,
    body: ChatRequest,
    session_id: str,
    source_id: int | None,
    video_name: str | None = None,
    *,
    messages: list[dict] | None = None,
    status: str = "running",
    error_message: str | None = None,
) -> int | None:
    exact_messages = messages or []
    return create_call_log(
        bind,
        request_id=uuid.uuid4().hex,
        user_id=current.id,
        username_snapshot=current.username,
        session_id=session_id,
        course_id=body.course_id,
        video_name=video_name,
        source_id=source_id,
        start_time=body.start_time,
        user_question=body.user_question,
        request_messages_json=json.dumps(exact_messages, ensure_ascii=False),
        prompt_chars=sum(len(str(item.get("content", ""))) for item in exact_messages),
        status=status,
        error_message=error_message,
    )


def _message_dict(message: ChatMessage) -> dict:
    payload = {
        "id": message.id,
        "turn_id": message.turn_id,
        "role": message.role,
        "content": message.content,
        "course_id": message.course_id,
        "video_name": message.video_name,
        "start_time": message.start_time,
        "model_name": message.model_name,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }
    if message.role == "assistant":
        try:
            thinking_ms = json.loads(message.context_meta_json or "{}").get("thinking_ms")
            if isinstance(thinking_ms, int) and thinking_ms >= 0:
                payload["thinking_ms"] = thinking_ms
        except (TypeError, json.JSONDecodeError):
            pass
    return payload


def _import_legacy_column_history(
    db: Session,
    column_session: ColumnChatSession,
    canonical_session: ChatSession,
) -> None:
    """Recover only the recent history still present in old course sessions."""
    legacy_sessions = (
        db.query(ChatSession)
        .join(Material, Material.course_id == ChatSession.course_id)
        .join(VideoKnowledge, VideoKnowledge.material_id == Material.id)
        .filter(
            ChatSession.user_id == column_session.user_id,
            VideoKnowledge.source_id == column_session.source_id,
            ChatSession.id != canonical_session.id,
        )
        .order_by(ChatSession.created_at.asc(), ChatSession.id.asc())
        .all()
    )
    for legacy in legacy_sessions:
        material = db.query(Material).filter(Material.course_id == legacy.course_id).first()
        try:
            items = json.loads(legacy.messages_json or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        for index in range(0, len(items) - 1, 2):
            user_item, assistant_item = items[index : index + 2]
            if user_item.get("role") != "user" or assistant_item.get("role") != "assistant":
                continue
            meta = assistant_item.get("context_meta") or {}
            turn_id = f"legacy-{legacy.id}-{index // 2}"
            common = {
                "session_id": canonical_session.session_id,
                "turn_id": turn_id,
                "course_id": legacy.course_id,
                "video_name": material.video_original_filename if material else None,
                "start_time": meta.get("start_time"),
            }
            db.add(ChatMessage(role="user", content=user_item.get("content", ""), **common))
            db.add(
                ChatMessage(
                    role="assistant",
                    content=assistant_item.get("content", ""),
                    model_name=assistant_item.get("model_name"),
                    context_meta_json=json.dumps(
                        {
                            "context_meta": meta,
                            "attempted_models": assistant_item.get("attempted_models") or [],
                            "fallback_count": assistant_item.get("fallback_count", 0),
                        },
                        ensure_ascii=False,
                    ),
                    **common,
                )
            )


def _get_or_create_column_session(
    db: Session, user_id: int, source: ProjectSource
) -> tuple[ColumnChatSession, ChatSession]:
    column_session = db.query(ColumnChatSession).filter(
        ColumnChatSession.user_id == user_id,
        ColumnChatSession.source_id == source.id,
    ).first()
    if column_session is not None:
        session = db.query(ChatSession).filter(
            ChatSession.session_id == column_session.session_id
        ).first()
        if session is None:
            raise HTTPException(status_code=409, detail="专栏会话数据不完整，请联系管理员")
        return column_session, session

    session = ChatSession(
        session_id=uuid.uuid4().hex,
        user_id=user_id,
        course_id=None,
        messages_json="[]",
    )
    db.add(session)
    db.flush()
    column_session = ColumnChatSession(
        user_id=user_id,
        source_id=source.id,
        session_id=session.session_id,
    )
    db.add(column_session)
    db.flush()
    _import_legacy_column_history(db, column_session, session)
    db.flush()
    imported = _column_messages(db, session.session_id)
    session.messages_json = json.dumps(_compatibility_mirror(imported), ensure_ascii=False)
    db.add(session)
    db.commit()
    db.refresh(column_session)
    return column_session, session


def _column_messages(db: Session, session_id: str) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.asc())
        .all()
    )


def _compatibility_mirror(messages: list[ChatMessage]) -> list[dict]:
    mirror = []
    for item in messages[-RECENT_COLUMN_MESSAGES:]:
        saved = {"role": item.role, "content": item.content}
        if item.model_name:
            saved["model_name"] = item.model_name
            try:
                saved.update(json.loads(item.context_meta_json or "{}"))
            except json.JSONDecodeError:
                pass
        mirror.append(saved)
    return mirror


def _pending_memory_text(messages: list[ChatMessage]) -> str:
    if not messages:
        return ""
    lines = ["以下是尚未归纳的较早对话："]
    for item in messages:
        label = "用户" if item.role == "user" else "助手"
        lines.append(f"{label}：{item.content}")
    return "\n".join(lines)


async def _maybe_update_memory(
    db: Session,
    column_session: ColumnChatSession,
    all_messages: list[ChatMessage],
    config: ModelConfig,
    api_key: str,
) -> list[ChatMessage]:
    old_messages = all_messages[:-RECENT_COLUMN_MESSAGES]
    pending = [
        item
        for item in old_messages
        if item.id > (column_session.summarized_through_message_id or 0)
    ]
    if len(pending) < SUMMARY_BATCH_MESSAGES:
        return pending

    summary_input = "\n".join(
        f"{'用户' if item.role == 'user' else '助手'}：{item.content}" for item in pending
    )
    summary_messages = [
        {
            "role": "system",
            "content": (
                "你负责增量维护专栏对话的长期记忆。只保留用户目标、已确认结论、关键术语、"
                "稳定偏好和未解决问题；不要补充原对话没有的事实，控制在约 3000 Token 内。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"【已有长期记忆】\n{column_session.memory_summary or '无'}\n\n"
                f"【待归纳对话】\n{summary_input}\n\n请输出更新后的完整长期记忆。"
            ),
        },
    ]
    outcome = RoutingOutcome()
    try:
        async for _ in stream_model_chain(
            db, config, api_key, summary_messages, outcome, stream_chat
        ):
            pass
    except Exception:
        return pending
    if not outcome.success or not outcome.answer.strip():
        return pending
    column_session.memory_summary = outcome.answer.strip()
    column_session.summarized_through_message_id = pending[-1].id
    db.add(column_session)
    db.commit()
    return []


@router.get("/column-session")
def get_column_session(
    course_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    material = db.query(Material).filter(Material.course_id == course_id).first()
    knowledge = (
        db.query(VideoKnowledge).filter(VideoKnowledge.material_id == material.id).first()
        if material
        else None
    )
    source = (
        db.query(ProjectSource).filter(
            ProjectSource.id == knowledge.source_id,
            ProjectSource.status == "active",
        ).first()
        if knowledge and knowledge.source_id
        else None
    )
    if source is None:
        raise HTTPException(status_code=404, detail="当前视频尚未归入专栏")
    column_session, session = _get_or_create_column_session(db, current.id, source)
    messages = _column_messages(db, session.session_id)
    return {
        "session_id": session.session_id,
        "column": {
            "source_id": source.id,
            "name": Path(source.original_filename).stem,
            "current_video_name": material.video_original_filename or course_id,
        },
        "messages": [_message_dict(item) for item in messages],
        "memory": {
            "has_summary": bool(column_session.memory_summary.strip()),
            "summarized_through_message_id": column_session.summarized_through_message_id,
            "legacy_history_may_be_incomplete": True,
        },
    }


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_started_at = perf_counter()
    # 课程与视频上下文
    courseware_text = ""
    courseware_has_chapters = False
    transcript = ""
    material = None
    video_knowledge = None
    video_source = None
    source_outline = None
    if body.course_id:
        material = db.query(Material).filter(Material.course_id == body.course_id).first()
        if material:
            video_knowledge = db.query(VideoKnowledge).filter(
                VideoKnowledge.material_id == material.id
            ).first()
            if video_knowledge and video_knowledge.source_id:
                video_source = db.query(ProjectSource).filter(
                    ProjectSource.id == video_knowledge.source_id,
                    ProjectSource.status == "active",
                ).first()
                if video_source is not None:
                    source_outline = db.query(ProjectSourceOutline).filter(
                        ProjectSourceOutline.source_id == video_source.id
                    ).first()
            courseware_text = material.courseware_text_cached or ""
            courseware_has_chapters = material.courseware_has_chapters
            # A3：时间窗基准 = 当前播放位置，缺省回退选中字幕起点（兼容老客户端）
            window_anchor = body.current_time if body.current_time is not None else body.start_time
            if material.subtitle_path and window_anchor is not None:
                # A3 门控：生成完成(ready) ≠ 可以作为自动证据(reviewed)。
                # 未审核字幕允许展示、允许主动引用，但不自动注入 ±180 秒上下文。
                if not transcript_context_allowed(material.subtitle_status, material.review_state):
                    transcript = ""
                else:
                    try:
                        vtt_text = Path(material.subtitle_path).read_text(encoding="utf-8")
                        cues = parse_vtt_cues(vtt_text)
                        transcript = extract_time_window(cues, window_anchor)
                    except Exception:
                        transcript = ""

    # 已归栏视频始终解析为当前用户在该专栏的固定会话。
    history: list[dict] = []
    session = None
    column_session = None
    complete_messages: list[ChatMessage] = []
    if video_source is not None:
        column_session, session = _get_or_create_column_session(db, current.id, video_source)
        if body.session_id and body.session_id != session.session_id:
            requested = db.query(ChatSession).filter(
                ChatSession.session_id == body.session_id
            ).first()
            if requested is not None and requested.user_id != current.id:
                raise HTTPException(status_code=403, detail="无权访问该会话")
            raise HTTPException(status_code=409, detail="会话不属于当前专栏，请刷新后重试")
        complete_messages = _column_messages(db, session.session_id)
        history = [
            {"role": item.role, "content": item.content}
            for item in complete_messages[-RECENT_COLUMN_MESSAGES:]
        ]
    elif body.session_id:
        session = db.query(ChatSession).filter(ChatSession.session_id == body.session_id).first()
        if session and session.user_id != current.id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
        if session:
            if session.course_id is None and body.course_id:
                # 用条件 UPDATE 原子完成首次课程绑定，避免并发请求分别绑定 A/B。
                updated = (
                    db.query(ChatSession)
                    .filter(
                        ChatSession.id == session.id,
                        ChatSession.course_id.is_(None),
                    )
                    .update(
                        {ChatSession.course_id: body.course_id},
                        synchronize_session=False,
                    )
                )
                if updated == 1:
                    session.course_id = body.course_id
                else:
                    db.expire(session)
                    db.refresh(session)
                    if session.course_id != body.course_id:
                        raise HTTPException(status_code=409, detail="会话与当前课程不匹配，请新建会话")
            elif session.course_id != body.course_id:
                raise HTTPException(status_code=409, detail="会话与当前课程不匹配，请新建会话")
            history = json.loads(session.messages_json or "[]")

    session_id = session.session_id if session is not None else (body.session_id or uuid.uuid4().hex)
    audit_bind = db.get_bind()
    binding = db.query(ChatContextBinding).filter(
        ChatContextBinding.session_id == session_id
    ).first()
    column_outline = ""
    if video_knowledge is not None and video_source is not None:
        # 已归栏视频统一使用专栏总大纲和本视频页原文；课程类型只用于后台分类。
        courseware_text = video_knowledge.knowledge_text_cached or ""
        courseware_has_chapters = False
        if (
            source_outline is not None
            and source_outline.status == "ready"
            and source_outline.source_hash == video_source.source_hash
        ):
            column_outline = source_outline.outline_text
        project_summary = ""
        project_evidence = ""
        context_meta = {
            "course_type": video_knowledge.course_type,
            "column_outline_included": bool(column_outline),
            "source_id": video_source.id,
            "source_hash": video_source.source_hash,
            "page_start": video_knowledge.page_start,
            "page_end": video_knowledge.page_end,
            "courseware_text_included": bool(courseware_text.strip()),
            "subtitle_included_in_knowledge": video_knowledge.subtitle_included,
        }
    elif binding is not None:
        # 已开始的旧会话继续固定原 Summary，避免管理员分类后多轮上下文突然漂移。
        project_summary, project_evidence, context_meta = version_context(
            db, binding, body.user_question
        )
    else:
        # 兼容尚未建立视频知识配置的旧数据与旧会话。
        project = get_project_for_material(db, material)
        if project is not None:
            published = latest_published_version(db, project.id)
            if published is not None:
                binding = ChatContextBinding(
                    session_id=session_id,
                    project_id=project.id,
                    context_version_id=published.id,
                )
        project_summary, project_evidence, context_meta = version_context(
            db, binding, body.user_question
        )
    if video_source is not None and not courseware_text.strip():
        _create_chat_audit(
            audit_bind,
            current,
            body,
            session_id,
            video_source.id,
            material.video_original_filename if material else None,
            status="rejected",
            error_message="当前视频课程证据尚未配置或课件已更新",
        )
        async def missing_courseware_gen():
            yield "data: " + json.dumps(
                {"error": "当前视频课程证据尚未配置或课件已更新，请联系管理员重新选择页区间并生成课程文本"},
                ensure_ascii=False,
            ) + "\n\n"
        return StreamingResponse(missing_courseware_gen(), media_type="text/event-stream")
    title = (
        material.video_original_filename
        if material and material.video_original_filename
        else body.course_id or "未绑定课程"
    )
    # A3：「当前播放位置」应为 current_time。老前端不传该字段时回退 start_time，
    # 避免把 Citation 区间错当成播放位置（PRD 指出的语义混乱点）。
    playback_time = body.current_time if body.current_time is not None else body.start_time
    time_text = f"{playback_time:.1f} 秒" if playback_time is not None else "未提供"
    duration_text = f"{body.video_duration:.1f} 秒" if body.video_duration else "未知"
    video_context = (
        f"课程ID：{body.course_id or '未绑定'}\n"
        f"视频：{title}\n当前播放位置：{time_text}\n视频总时长：{duration_text}\n"
        + (
            f"课程类型：{'实战/案例' if video_knowledge.course_type == 'practice' else '理论/通用'}。\n"
            if video_knowledge is not None
            else ""
        )
        + (
            "当前已提供审核字幕时间窗，可用于理解附近讲解。"
            if transcript
            else "当前没有可用的审核字幕或时间轴，播放位置仅作记录，不能据此推断声音或画面。"
        )
    )

    # 学习端始终使用管理员选定的默认 API，不能通过请求参数切换凭据。
    cfg = get_default_config(db)
    if cfg is None:
        _create_chat_audit(
            audit_bind,
            current,
            body,
            session_id,
            video_source.id if video_source else None,
            title,
            status="rejected",
            error_message="未配置大模型",
        )
        async def no_cfg_gen():
            yield "data: " + json.dumps({"error": "未配置大模型，请联系管理员"}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(no_cfg_gen(), media_type="text/event-stream")

    api_key = decrypt_api_key(cfg.api_key_encrypted)
    if not api_key:
        _create_chat_audit(
            audit_bind,
            current,
            body,
            session_id,
            video_source.id if video_source else None,
            title,
            status="rejected",
            error_message="大模型 API Key 无效",
        )
        async def bad_key_gen():
            yield "data: " + json.dumps({"error": "大模型 API Key 无效，请联系管理员检查配置"}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(bad_key_gen(), media_type="text/event-stream")

    memory_summary = ""
    if column_session is not None:
        pending_memory = await _maybe_update_memory(
            db, column_session, complete_messages, cfg, api_key
        )
        memory_parts = [column_session.memory_summary.strip(), _pending_memory_text(pending_memory)]
        memory_summary = "\n\n".join(part for part in memory_parts if part)

    messages, notice = build_context(
        courseware_text=courseware_text,
        courseware_has_chapters=courseware_has_chapters,
        transcript=transcript,
        selected_subtitle=body.selected_subtitle,
        question=body.user_question,
        history=history,
        start_time=body.start_time,
        # 无字幕时不能按播放比例猜课件章节；有逐字稿才允许现有时间映射逻辑。
        video_duration=body.video_duration if transcript else None,
        project_summary=project_summary,
        project_evidence=project_evidence,
        column_outline=column_outline,
        memory_summary=memory_summary,
        video_context=video_context,
    )
    if not messages:
        # Token 超限拒绝
        _create_chat_audit(
            audit_bind,
            current,
            body,
            session_id,
            video_source.id if video_source else None,
            title,
            status="rejected",
            error_message=notice,
        )
        async def reject_gen():
            yield "data: " + json.dumps({"error": notice}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(reject_gen(), media_type="text/event-stream")

    config_id = cfg.id
    stream_bind = audit_bind

    # 会话落库
    created_session = session is None
    if session is None:
        session = ChatSession(
            session_id=session_id,
            user_id=current.id,
            course_id=body.course_id,
            selected_subtitle=body.selected_subtitle,
            selected_subtitle_start=body.start_time,
            selected_subtitle_end=body.end_time,
            messages_json=json.dumps(history, ensure_ascii=False),
            model_config_id=cfg.id,
        )
        db.add(session)
        db.flush()
    # 持久化本轮构造 messages 时已经选中的同一版本，不能再次查询 latest。
    # 否则管理员在两个步骤之间发布新版时，会出现“首答用 V1、会话绑定 V2”。
    persisted_binding = db.query(ChatContextBinding).filter(
        ChatContextBinding.session_id == session_id
    ).first()
    if persisted_binding is None and binding is not None:
        db.add(binding)
        db.flush()
        persisted_binding = binding
    db.commit()
    audit_log_id = _create_chat_audit(
        audit_bind,
        current,
        body,
        session_id,
        video_source.id if video_source else None,
        title,
        messages=messages,
    )
    turn_id = uuid.uuid4().hex

    async def event_stream():
        outcome = RoutingOutcome(request_started_at=request_started_at)
        stream_db = Session(bind=stream_bind)
        terminal_status = "interrupted"
        terminal_error = "客户端中断或流式响应未完成"
        try:
            # 先发提示信息（如有截断提示）
            if notice:
                yield "data: " + json.dumps({"notice": notice}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"session_id": session_id}, ensure_ascii=False) + "\n\n"
            stream_config = stream_db.get(ModelConfig, config_id)
            if stream_config is None:
                terminal_status = "rejected"
                terminal_error = "默认模型配置已不存在"
                yield "data: " + json.dumps({"error": "默认模型配置已不存在"}, ensure_ascii=False) + "\n\n"
                return
            async for event in stream_model_chain(
                stream_db,
                stream_config,
                api_key,
                messages,
                outcome,
                stream_chat,
            ):
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            try:
                # 只保存最终成功模型的完整答案，中间失败片段不落库。
                if outcome.success:
                    assistant_message = {
                        "role": "assistant",
                        "content": outcome.answer,
                        "model_name": outcome.model_name,
                        "model_route_id": outcome.route_id,
                        "attempted_models": outcome.attempted_models,
                        "fallback_count": outcome.fallback_count,
                        "thinking_ms": outcome.thinking_ms,
                        "context_meta": {
                            **context_meta,
                            "course_id": body.course_id,
                            "start_time": body.start_time,
                            "video_duration": body.video_duration,
                            "subtitle_context": bool(transcript),
                        },
                    }
                    if column_session is not None:
                        _append_column_turn(
                            stream_db,
                            session_id=session_id,
                            turn_id=turn_id,
                            user_message={"role": "user", "content": body.user_question},
                            assistant_message=assistant_message,
                            course_id=body.course_id,
                            video_name=title,
                            start_time=body.start_time,
                            model_config_id=config_id,
                        )
                    else:
                        _append_turn_atomic(
                            stream_db,
                            session_id,
                            {"role": "user", "content": body.user_question},
                            assistant_message,
                            config_id,
                        )
                elif created_session:
                    failed_binding = stream_db.query(ChatContextBinding).filter(
                        ChatContextBinding.session_id == session_id
                    ).first()
                    failed_session = stream_db.query(ChatSession).filter(
                        ChatSession.session_id == session_id
                    ).first()
                    if failed_binding is not None:
                        stream_db.delete(failed_binding)
                    if failed_session is not None:
                        stream_db.delete(failed_session)
                    stream_db.commit()
            finally:
                if outcome.success:
                    update_call_log(
                        stream_bind,
                        audit_log_id,
                        status="success",
                        attempted_models_json=json.dumps(
                            outcome.attempted_models, ensure_ascii=False
                        ),
                        final_model_name=outcome.model_name,
                        fallback_count=outcome.fallback_count,
                        answer_text=outcome.answer,
                        answer_chars=len(outcome.answer),
                    )
                elif outcome.last_error is not None:
                    update_call_log(
                        stream_bind,
                        audit_log_id,
                        status="failed",
                        attempted_models_json=json.dumps(
                            outcome.attempted_models, ensure_ascii=False
                        ),
                        fallback_count=outcome.fallback_count,
                        error_category=outcome.last_error.category.value,
                        error_code=outcome.last_error.provider_code,
                        error_message=outcome.last_error.safe_message,
                    )
                elif outcome.rejection_message:
                    update_call_log(
                        stream_bind,
                        audit_log_id,
                        status="rejected",
                        attempted_models_json="[]",
                        fallback_count=0,
                        error_message=outcome.rejection_message,
                    )
                else:
                    update_call_log(
                        stream_bind,
                        audit_log_id,
                        status=terminal_status,
                        attempted_models_json=json.dumps(
                            outcome.attempted_models, ensure_ascii=False
                        ),
                        fallback_count=outcome.fallback_count,
                        error_message=terminal_error,
                    )
                stream_db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _append_column_turn(
    db: Session,
    *,
    session_id: str,
    turn_id: str,
    user_message: dict,
    assistant_message: dict,
    course_id: str | None,
    video_name: str,
    start_time: float | None,
    model_config_id: int,
) -> None:
    """Persist one successful final pair and update only the recent compatibility mirror."""
    common = {
        "session_id": session_id,
        "turn_id": turn_id,
        "course_id": course_id,
        "video_name": video_name,
        "start_time": start_time,
    }
    db.add(ChatMessage(role="user", content=user_message["content"], **common))
    db.add(
        ChatMessage(
            role="assistant",
            content=assistant_message["content"],
            model_name=assistant_message.get("model_name"),
            context_meta_json=json.dumps(
                {
                    "context_meta": assistant_message.get("context_meta") or {},
                    "model_route_id": assistant_message.get("model_route_id"),
                    "attempted_models": assistant_message.get("attempted_models") or [],
                    "fallback_count": assistant_message.get("fallback_count", 0),
                    "thinking_ms": assistant_message.get("thinking_ms"),
                },
                ensure_ascii=False,
            ),
            **common,
        )
    )
    db.flush()
    recent = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(RECENT_COLUMN_MESSAGES)
        .all()
    )
    mirror = _compatibility_mirror(list(reversed(recent)))
    session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
    if session is not None:
        session.messages_json = json.dumps(mirror, ensure_ascii=False)
        session.model_config_id = model_config_id
        db.add(session)
    db.commit()


def _append_turn_atomic(
    db: Session,
    session_id: str,
    user_message: dict,
    assistant_message: dict,
    model_config_id: int,
) -> None:
    """用 messages_json 乐观 CAS 合并并发完成的轮次，避免 last-writer-wins。"""
    for _ in range(SESSION_WRITE_RETRIES):
        db.expire_all()
        current = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        if current is None:
            return
        previous_json = current.messages_json or "[]"
        current_history = json.loads(previous_json)
        next_history = (current_history + [user_message, assistant_message])[-10:]
        updated = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == current.id,
                ChatSession.messages_json == previous_json,
            )
            .update(
                {
                    ChatSession.messages_json: json.dumps(next_history, ensure_ascii=False),
                    ChatSession.model_config_id: model_config_id,
                },
                synchronize_session=False,
            )
        )
        if updated == 1:
            db.commit()
            return
        db.rollback()
    raise RuntimeError("会话并发写入冲突，请重试本轮提问")


@router.post("/sessions/{session_id}/clear")
def clear_session(
    session_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.user_id != current.id:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    column_session = db.query(ColumnChatSession).filter(
        ColumnChatSession.session_id == session_id
    ).first()
    if column_session is not None:
        db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete(
            synchronize_session=False
        )
        column_session.memory_summary = ""
        column_session.summarized_through_message_id = None
        db.add(column_session)
    session.messages_json = "[]"
    db.add(session)
    db.commit()
    return {"message": "会话已清空", "session_id": session_id}


@router.get("/sessions")
def list_sessions(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == current.id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    return [
        {
            "session_id": s.session_id,
            "course_id": s.course_id,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in sessions
    ]
