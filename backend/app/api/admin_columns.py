"""Admin CRUD for independent content series."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.models import (
    ChatContextBinding,
    ChatMessage,
    ChatSession,
    ColumnChatSession,
    ContentSeries,
    LLMCallLog,
    ProjectSourceOutline,
    User,
    VideoKnowledge,
)
from app.services.project_context import (
    current_series_source,
    ensure_default_project,
    normalize_series_name,
    ppt_pages,
)

router = APIRouter(prefix="/api/admin/columns", tags=["admin-columns"])


class SeriesNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


def _series_payload(db: Session, series: ContentSeries) -> dict:
    source = current_series_source(db, series.id)
    outline = (
        db.query(ProjectSourceOutline).filter(ProjectSourceOutline.source_id == source.id).first()
        if source
        else None
    )
    return {
        "id": series.id,
        "name": series.name,
        "context_epoch": series.context_epoch,
        "video_count": db.query(VideoKnowledge).filter(VideoKnowledge.series_id == series.id).count(),
        "source": None if source is None else {
            "id": source.id,
            "filename": source.original_filename,
            "format": source.source_format,
            "sha256": source.source_hash,
            "page_count": len(ppt_pages(source)),
            "outline_text": outline.outline_text if outline else "",
            "outline_status": outline.status if outline else "empty",
            "outline_updated_at": outline.updated_at.isoformat() if outline and outline.updated_at else None,
        },
        "created_at": series.created_at.isoformat() if series.created_at else None,
        "updated_at": series.updated_at.isoformat() if series.updated_at else None,
    }


def _mirror_has_messages(raw: str | None) -> bool:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return True
    return bool(value)


@router.get("")
def list_columns(
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    db.commit()
    rows = db.query(ContentSeries).filter(
        ContentSeries.project_id == project.id
    ).order_by(ContentSeries.id.asc()).all()
    return [_series_payload(db, row) for row in rows]


@router.post("")
def create_column(
    body: SeriesNameRequest,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    name, normalized = normalize_series_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="专栏名称不能为空")
    row = ContentSeries(project_id=project.id, name=name, normalized_name=normalized)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名专栏已存在") from None
    db.refresh(row)
    return _series_payload(db, row)


@router.put("/{series_id}")
def rename_column(
    series_id: int,
    body: SeriesNameRequest,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.get(ContentSeries, series_id)
    if row is None:
        raise HTTPException(status_code=404, detail="专栏不存在")
    name, normalized = normalize_series_name(body.name)
    if not name:
        raise HTTPException(status_code=400, detail="专栏名称不能为空")
    row.name, row.normalized_name = name, normalized
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名专栏已存在") from None
    db.refresh(row)
    return _series_payload(db, row)


@router.delete("/{series_id}")
def delete_column(
    series_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.get(ContentSeries, series_id)
    if row is None:
        raise HTTPException(status_code=404, detail="专栏不存在")
    if current_series_source(db, series_id) is not None:
        raise HTTPException(status_code=409, detail="专栏仍有课件，不能删除")
    if db.query(VideoKnowledge.id).filter(VideoKnowledge.series_id == series_id).first():
        raise HTTPException(status_code=409, detail="专栏仍有视频，不能删除")

    bindings = db.query(ColumnChatSession).filter(ColumnChatSession.series_id == series_id).all()
    for binding in bindings:
        session = db.query(ChatSession).filter(ChatSession.session_id == binding.session_id).first()
        has_messages = db.query(ChatMessage.id).filter(
            ChatMessage.session_id == binding.session_id
        ).first() is not None
        mirror_has_messages = _mirror_has_messages(session.messages_json) if session else False
        if has_messages or binding.memory_summary.strip() or binding.summarized_through_message_id or mirror_has_messages:
            raise HTTPException(status_code=409, detail="专栏仍有真实会话内容，不能删除")
        if session and (session.selected_subtitle or session.selected_subtitle_start is not None):
            raise HTTPException(status_code=409, detail="专栏仍有会话上下文，不能删除")

    db.query(LLMCallLog).filter(LLMCallLog.series_id == series_id).update(
        {LLMCallLog.series_id: None}, synchronize_session=False
    )
    for binding in bindings:
        db.query(ChatContextBinding).filter(
            ChatContextBinding.session_id == binding.session_id
        ).delete(synchronize_session=False)
        session = db.query(ChatSession).filter(ChatSession.session_id == binding.session_id).first()
        db.delete(binding)
        if session:
            db.delete(session)
    db.delete(row)
    db.commit()
    return {"message": "专栏已删除"}
