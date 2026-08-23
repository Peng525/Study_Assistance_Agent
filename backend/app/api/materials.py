"""素材公开接口（课程列表/视频流/字幕/字幕状态）。"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.models import Material, User
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
    return [
        {
            "course_id": m.course_id,
            "status": m.status,
            "error_message": m.error_message,
            "courseware_format": m.courseware_format,
            "subtitle_status": m.subtitle_status,
            "title": _extract_title(m.courseware_text_cached),
            "duration": _extract_duration(m.subtitle_path),
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
    }


@router.get("/{course_id}/video")
def get_video(course_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
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
