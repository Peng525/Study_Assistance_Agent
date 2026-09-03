"""Admin-only learner LLM call logs."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.models import LLMCallLog, User


router = APIRouter(prefix="/api/admin/llm-call-logs", tags=["admin"])


def _loads(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _summary(record: LLMCallLog) -> dict:
    return {
        "id": record.id,
        "request_id": record.request_id,
        "user_id": record.user_id,
        "username": record.username_snapshot,
        "session_id": record.session_id,
        "course_id": record.course_id,
        "video_name": record.video_name,
        "source_id": record.source_id,
        "start_time": record.start_time,
        "user_question": record.user_question,
        "prompt_chars": record.prompt_chars,
        "status": record.status,
        "attempted_models": _loads(record.attempted_models_json, []),
        "final_model_name": record.final_model_name,
        "fallback_count": record.fallback_count,
        "answer_chars": record.answer_chars,
        "error_category": record.error_category,
        "error_code": record.error_code,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
    }


@router.get("")
def list_llm_call_logs(
    user_id: int | None = Query(default=None, ge=1),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(LLMCallLog)
    if user_id is not None:
        query = query.filter(LLMCallLog.user_id == user_id)
    if status:
        query = query.filter(LLMCallLog.status == status)
    total = query.count()
    records = (
        query.order_by(LLMCallLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_summary(record) for record in records],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{log_id}")
def get_llm_call_log(
    log_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    record = db.get(LLMCallLog, log_id)
    if record is None:
        raise HTTPException(status_code=404, detail="AI 调用日志不存在")
    return {
        **_summary(record),
        "request_messages": _loads(record.request_messages_json, []),
        "answer_text": record.answer_text,
    }


@router.delete("")
def clear_llm_call_logs(
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    deleted = db.query(LLMCallLog).delete(synchronize_session=False)
    db.commit()
    return {"message": "AI 调用日志已清空", "deleted_count": deleted}
