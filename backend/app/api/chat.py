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
from app.models.models import ChatSession, Material, ModelConfig, User
from app.services.context_builder import build_context, parse_vtt_cues, extract_time_window
from app.services.llm_client import LLMError, stream_chat

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    course_id: str | None = None
    selected_subtitle: str = ""
    start_time: float | None = None
    end_time: float | None = None
    user_question: str = Field(min_length=1)
    session_id: str | None = None
    model_config_id: int | None = None


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 构造上下文
    courseware_text = ""
    courseware_has_chapters = False
    transcript = ""
    video_duration = None
    if body.course_id:
        material = db.query(Material).filter(Material.course_id == body.course_id).first()
        if material:
            courseware_text = material.courseware_text_cached or ""
            courseware_has_chapters = material.courseware_has_chapters
            if material.subtitle_path and body.start_time is not None:
                try:
                    vtt_text = Path(material.subtitle_path).read_text(encoding="utf-8")
                    cues = parse_vtt_cues(vtt_text)
                    transcript = extract_time_window(cues, body.start_time)
                    # 视频时长 ≈ 字幕最后一条 cue 的 end 时间
                    if cues:
                        video_duration = max(c["end"] for c in cues)
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
            history = json.loads(session.messages_json or "[]")

    messages, notice = build_context(
        courseware_text=courseware_text,
        courseware_has_chapters=courseware_has_chapters,
        transcript=transcript,
        selected_subtitle=body.selected_subtitle,
        question=body.user_question,
        history=history,
        start_time=body.start_time,
        video_duration=video_duration,
    )
    if not messages:
        # Token 超限拒绝
        async def reject_gen():
            yield "data: " + json.dumps({"error": notice}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(reject_gen(), media_type="text/event-stream")

    # 解析模型配置
    cfg = _resolve_config(db, body.model_config_id)
    if cfg is None:
        async def no_cfg_gen():
            yield "data: " + json.dumps({"error": "未配置大模型，请联系管理员"}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(no_cfg_gen(), media_type="text/event-stream")

    api_key = decrypt_api_key(cfg.api_key_encrypted)
    if not api_key:
        async def bad_key_gen():
            yield "data: " + json.dumps({"error": "大模型 API Key 无效，请联系管理员检查配置"}, ensure_ascii=False) + "\n\n"
        return StreamingResponse(bad_key_gen(), media_type="text/event-stream")

    # 会话落库
    if session is None:
        session_id = body.session_id or uuid.uuid4().hex
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
        db.commit()
        session_id = session.session_id
    else:
        session_id = session.session_id

    async def event_stream():
        full_answer = ""
        try:
            # 先发提示信息（如有截断提示）
            if notice:
                yield "data: " + json.dumps({"notice": notice}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"session_id": session_id}, ensure_ascii=False) + "\n\n"
            async for delta in stream_chat(cfg.base_url, api_key, cfg.model_name, messages):
                full_answer += delta
                yield "data: " + json.dumps({"delta": delta}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"done": True}, ensure_ascii=False) + "\n\n"
        except LLMError as e:
            yield "data: " + json.dumps({"error": e.message}, ensure_ascii=False) + "\n\n"
        finally:
            # 保存本轮对话到会话历史
            if full_answer or session is not None:
                new_history = history + [
                    {"role": "user", "content": body.user_question},
                    {"role": "assistant", "content": full_answer},
                ]
                session.messages_json = json.dumps(new_history[-10:], ensure_ascii=False)
                session.updated_at = session.updated_at  # noqa
                db.add(session)
                db.commit()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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


def _resolve_config(db: Session, config_id: int | None) -> ModelConfig | None:
    if config_id is not None:
        return db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    return get_default_config(db)
