"""素材公开接口（课程列表/视频流/字幕/字幕状态）。"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.models import Material, User
from app.services import whisper_service

router = APIRouter(prefix="/api/materials", tags=["materials"])


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
    return FileResponse(str(path), media_type="video/mp4")


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
