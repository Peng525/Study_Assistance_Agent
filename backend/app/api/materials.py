"""素材公开接口（课程列表/视频流/字幕/字幕状态）。"""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.config import settings
from app.core.security import create_media_ticket, decode_media_ticket
from app.models.models import Material, ProjectSource, User, VideoKnowledge
from app.services import whisper_service
from app.services.context_builder import parse_vtt_cues

router = APIRouter(prefix="/api/materials", tags=["materials"])


def _extract_title(courseware_text: str | None) -> str | None:
    """从课件缓存文本首行提取标题（# 开头则去掉 # 号）。"""
    if not courseware_text:
        return None
    for line in courseware_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.lstrip("#").strip()
    return None


def _extract_duration(subtitle_path: str | None) -> float | None:
    """从字幕文件末条 cue 的 end 提取视频时长（秒）。"""
    if not subtitle_path:
        return None
    try:
        vtt_text = Path(subtitle_path).read_text(encoding="utf-8")
        cues = parse_vtt_cues(vtt_text)
        if cues:
            return max(c["end"] for c in cues)
    except Exception:
        return None
    return None


@router.get("")
def list_materials(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(Material)
    if current.role != "admin":
        query = query.filter(Material.status == "ready")  # user 只看 ready
    materials = query.order_by(Material.id).all()
    knowledge_by_material = {
        item.material_id: item
        for item in db.query(VideoKnowledge).filter(
            VideoKnowledge.material_id.in_([material.id for material in materials])
        ).all()
    } if materials else {}
    source_ids = {
        knowledge.source_id for knowledge in knowledge_by_material.values() if knowledge.source_id
    }
    source_names = {
        source.id: source.original_filename
        for source in db.query(ProjectSource).filter(ProjectSource.id.in_(source_ids)).all()
    } if source_ids else {}
    return [
        {
            "course_id": m.course_id,
            "status": m.status,
            "error_message": m.error_message,
            "courseware_format": m.courseware_format,
            "subtitle_status": m.subtitle_status,
            "title": _extract_title(m.courseware_text_cached),
            "duration": _extract_duration(m.subtitle_path),
            "course_type": (
                knowledge_by_material[m.id].course_type
                if m.id in knowledge_by_material
                else None
            ),
            "source_id": knowledge_by_material[m.id].source_id if m.id in knowledge_by_material else None,
            "source_filename": source_names.get(knowledge_by_material[m.id].source_id)
            if m.id in knowledge_by_material
            else None,
            "scanned_at": m.scanned_at.isoformat() if m.scanned_at else None,
        }
        for m in materials
    ]


@router.get("/{course_id}")
def get_material(course_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    if material.status != "ready" and current.role != "admin":
        raise HTTPException(status_code=404, detail="课程不可用")
    return {
        "course_id": material.course_id,
        "status": material.status,
        "courseware_format": material.courseware_format,
        "subtitle_status": material.subtitle_status,
        "course_type": (
            db.query(VideoKnowledge.course_type)
            .filter(VideoKnowledge.material_id == material.id)
            .scalar()
        ),
    }


@router.get("/{course_id}/video")
def get_video(course_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _video_response(course_id, db)


@router.post("/{course_id}/playback-ticket")
def create_playback_ticket(
    course_id: str,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None or material.video_path is None or not Path(material.video_path).exists():
        raise HTTPException(status_code=404, detail="视频不存在")
    ticket = create_media_ticket(current.id, course_id)
    encoded_course = quote(course_id, safe="")
    return {
        "url": f"/api/materials/{encoded_course}/video-playback?ticket={ticket}",
        "expires_in": settings.media_ticket_ttl_seconds,
    }


@router.get("/{course_id}/video-playback")
def get_video_with_ticket(course_id: str, ticket: str, db: Session = Depends(get_db)):
    payload = decode_media_ticket(ticket, course_id)
    if payload is None:
        raise HTTPException(status_code=401, detail="播放凭证无效或已过期")
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="播放凭证无效") from None
    if db.query(User).filter(User.id == user_id).first() is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return _video_response(course_id, db)


def _video_response(course_id: str, db: Session):
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None or material.video_path is None:
        raise HTTPException(status_code=404, detail="视频不存在")
    path = Path(material.video_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="视频文件缺失")
    # 按扩展名动态设置 media_type（mp4/webm）
    media_type = "video/mp4" if path.suffix.lower() == ".mp4" else "video/webm"
    return FileResponse(str(path), media_type=media_type)


@router.get("/{course_id}/subtitle")
def get_subtitle(course_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None or material.subtitle_path is None:
        raise HTTPException(status_code=404, detail="字幕不存在")
    path = Path(material.subtitle_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="字幕文件缺失")
    return FileResponse(str(path), media_type="text/vtt")


@router.get("/{course_id}/subtitle-status")
def get_subtitle_status(course_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    status = whisper_service.get_status(course_id)
    return {
        "course_id": course_id,
        "subtitle_status": material.subtitle_status,
        **status,
    }
