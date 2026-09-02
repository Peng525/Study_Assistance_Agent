"""AI 对话接口（SSE 流式 + 会话管理）。"""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.admin_model_configs import get_default_config
from app.core.database import get_db
from app.core.security import decrypt_api_key
from app.models.models import (
    ChatContextBinding,
    ChatSession,
    Material,
    ModelConfig,
    User,
    VideoKnowledge,
)
from app.services.context_builder import build_context, parse_vtt_cues, extract_time_window
from app.services.llm_client import stream_chat
from app.services.model_router import RoutingOutcome, stream_model_chain
from app.services.project_context import (
    get_project_for_material,
    latest_published_version,
    version_context,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
SESSION_WRITE_RETRIES = 5


class ChatRequest(BaseModel):
    course_id: str | None = None
    selected_subtitle: str = ""
    start_time: float | None = None
    end_time: float | None = None
    video_duration: float | None = Field(default=None, ge=0)
    user_question: str = Field(min_length=1)
    session_id: str | None = None


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 课程与视频上下文
    courseware_text = ""
    courseware_has_chapters = False
    transcript = ""
    material = None
    video_knowledge = None
    if body.course_id:
        material = db.query(Material).filter(Material.course_id == body.course_id).first()
        if material:
            video_knowledge = db.query(VideoKnowledge).filter(
                VideoKnowledge.material_id == material.id
            ).first()
            courseware_text = material.courseware_text_cached or ""
            courseware_has_chapters = material.courseware_has_chapters
            if material.subtitle_path and body.start_time is not None:
                try:
                    vtt_text = Path(material.subtitle_path).read_text(encoding="utf-8")
                    cues = parse_vtt_cues(vtt_text)
                    transcript = extract_time_window(cues, body.start_time)
                except Exception:
                    transcript = ""

    # 会话历史
    history: list[dict] = []
    session = None
    if body.session_id:
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
    binding = db.query(ChatContextBinding).filter(
        ChatContextBinding.session_id == session_id
    ).first()
    video_outline = ""
    if binding is not None:
        # 已开始的旧会话继续固定原 Summary，避免管理员分类后多轮上下文突然漂移。
        project_summary, project_evidence, context_meta = version_context(
            db, binding, body.user_question
        )
    elif video_knowledge is not None:
        # 新的视频级知识流：理论/通用课程不注入大纲；实战/案例只注入该视频大纲。
        courseware_text = ""
        courseware_has_chapters = False
        if (
            video_knowledge.course_type == "practice"
            and video_knowledge.outline_status == "ready"
        ):
            video_outline = video_knowledge.outline_text_cached or ""
        project_summary = ""
        project_evidence = ""
        context_meta = {
            "course_type": video_knowledge.course_type,
            "video_outline_included": bool(video_outline),
            "video_knowledge_source_id": video_knowledge.source_id,
            "video_knowledge_page_start": video_knowledge.page_start,
            "video_knowledge_page_end": video_knowledge.page_end,
            "subtitle_included_in_knowledge": video_knowledge.subtitle_included,
        }
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
    title = (
        material.video_original_filename
        if material and material.video_original_filename
        else body.course_id or "未绑定课程"
    )
    time_text = f"{body.start_time:.1f} 秒" if body.start_time is not None else "未提供"
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
        video_outline=video_outline,
        video_context=video_context,
    )
    if not messages:
        # Token 超限拒绝
        async def reject_gen():
            yield "data: " + json.dumps({"error": notice}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(reject_gen(), media_type="text/event-stream")

    # 学习端始终使用管理员选定的默认 API，不能通过请求参数切换凭据。
    cfg = get_default_config(db)
    if cfg is None:
        async def no_cfg_gen():
            yield "data: " + json.dumps({"error": "未配置大模型，请联系管理员"}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(no_cfg_gen(), media_type="text/event-stream")

    api_key = decrypt_api_key(cfg.api_key_encrypted)
    if not api_key:
        async def bad_key_gen():
            yield "data: " + json.dumps({"error": "大模型 API Key 无效，请联系管理员检查配置"}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(bad_key_gen(), media_type="text/event-stream")
    config_id = cfg.id
    stream_bind = db.get_bind()

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

    async def event_stream():
        outcome = RoutingOutcome()
        stream_db = Session(bind=stream_bind)
        try:
            # 先发提示信息（如有截断提示）
            if notice:
                yield "data: " + json.dumps({"notice": notice}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"session_id": session_id}, ensure_ascii=False) + "\n\n"
            stream_config = stream_db.get(ModelConfig, config_id)
            if stream_config is None:
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
                        "context_meta": {
                            **context_meta,
                            "course_id": body.course_id,
                            "start_time": body.start_time,
                            "video_duration": body.video_duration,
                            "subtitle_context": bool(transcript),
                        },
                    }
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
                stream_db.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
    session.messages_json = "[]"
    db.add(session)
    db.commit()
    return {"message": "会话已清空"}


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
