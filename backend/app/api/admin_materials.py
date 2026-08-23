"""管理台素材管理接口（上传/文件列表/删除/扫描/rescan）。"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.models import Material, User
from app.services import storage
from app.services import whisper_service
from app.services.courseware import extract_courseware
from app.services.subtitle import detect_unsupported_format, srt_to_vtt

router = APIRouter(prefix="/api/admin/materials", tags=["admin-materials"])

_ORIGINAL_FIELD = {
    "video": "video_original_filename",
    "subtitle": "subtitle_original_filename",
    "courseware": "courseware_original_filename",
}


@router.post("/upload")
async def upload(
    course_id: str,
    file_type: str,
    file: UploadFile,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if file_type not in storage.FILE_TYPES:
        raise HTTPException(status_code=400, detail="未知文件类型")

    original_filename = file.filename or ""
    err = storage.validate_filename(original_filename)
    if err:
        raise HTTPException(status_code=400, detail=err)

    cfg = storage.FILE_TYPES[file_type]
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if f".{ext}" not in cfg["exts"]:
        allowed = "/".join(sorted(cfg["exts"]))
        raise HTTPException(status_code=400, detail=f"仅支持 {allowed} 格式")

    # 读文件内容（分块，限流）
    content = await file.read()
    max_bytes = cfg["max_bytes"]
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail=f"{cfg['label']}文件过大，上限 {max_bytes // 1024 // 1024}MB")

    # magic number 校验
    head = content[:16]
    if storage.validate_magic(file_type, f".{ext}", head):
        raise HTTPException(status_code=400, detail="文件内容与扩展名不符")

    # 字幕格式额外校验
    if file_type == "subtitle":
        text_sample = content[:4096].decode("utf-8", errors="ignore")
        unsupported = detect_unsupported_format(text_sample)
        if unsupported:
            raise HTTPException(status_code=400, detail=unsupported)

    # 保存文件
    dest = storage.save_upload(course_id, file_type, original_filename, content)

    # 更新或创建 materials 记录
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        material = Material(course_id=course_id, dir_path=str(storage._course_dir(course_id)))
        db.add(material)
    setattr(material, _ORIGINAL_FIELD[file_type], original_filename)
    material.uploaded_at = datetime.now(timezone.utc)
    db.commit()

    # 上传后自动 rescan 刷新缓存
    _rescan_material(db, material)

    return {
        "message": f"{cfg['label']}上传成功",
        "filename": original_filename,
        "path": str(dest),
        "course_id": course_id,
    }


@router.get("/{course_id}/files")
def list_files(
    course_id: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    files = storage.list_course_files(course_id)
    return {"course_id": course_id, "files": files}


@router.delete("/{course_id}/files/{file_type}")
def delete_file(
    course_id: str,
    file_type: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if file_type not in storage.FILE_TYPES:
        raise HTTPException(status_code=400, detail="未知文件类型")
    deleted = storage.delete_course_file(course_id, file_type)
    if not deleted:
        raise HTTPException(status_code=404, detail="文件不存在")
    # 清理对应字段并重扫
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material:
        path_field = {
            "video": "video_path",
            "subtitle": "subtitle_path",
            "courseware": "courseware_path",
        }[file_type]
        setattr(material, path_field, None)
        setattr(material, _ORIGINAL_FIELD[file_type], None)
        db.commit()
        _rescan_material(db, material)
    return {"message": "已删除"}


@router.post("/scan")
def scan_all(
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """全量扫描 ./materials/（方案 B 备选）。"""
    root = storage._materials_root()
    if not root.exists():
        return {"message": "素材目录为空", "count": 0}
    count = 0
    for course_dir in root.iterdir():
        if course_dir.is_dir():
            _scan_and_upsert(db, course_dir.name)
            count += 1
    return {"message": f"已扫描 {count} 个课程", "count": count}


@router.post("/{course_id}/rescan")
def rescan(
    course_id: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    _rescan_material(db, material)
    return {"message": "重新扫描完成", "status": material.status}


@router.post("/{course_id}/generate-subtitle")
def generate_subtitle(
    course_id: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """手动触发 Whisper 字幕生成（失败后重试）。"""
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    if material.video_path is None:
        raise HTTPException(status_code=400, detail="该课程无视频文件")
    if not whisper_service.is_ffmpeg_available():
        raise HTTPException(
            status_code=400,
            detail="未检测到 ffmpeg，请先安装并加入 PATH（Whisper 依赖）",
        )
    result = whisper_service.enqueue(course_id, material.video_path)
    material.subtitle_status = "generating" if result["status"] == "generating" else "pending"
    material.subtitle_source = "whisper"
    db.commit()
    return result


@router.post("/{course_id}/cancel-subtitle")
def cancel_subtitle(
    course_id: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """取消排队中的生成任务（仅 pending）。"""
    status = whisper_service.get_status(course_id)
    if status["status"] != "pending":
        raise HTTPException(status_code=400, detail="仅排队中的任务可取消")
    cancelled = whisper_service.cancel(course_id)
    if cancelled:
        material = db.query(Material).filter(Material.course_id == course_id).first()
        if material:
            material.subtitle_status = "pending"
            material.subtitle_source = None
            db.commit()
    return {"message": "已取消"}


@router.get("/whisper/model-status")
def whisper_model_status(current: User = Depends(require_admin)):
    return {
        "ffmpeg_available": whisper_service.is_ffmpeg_available(),
        "model": "medium",
        "active_tasks": whisper_service.active_task_count(),
    }


# ---- 内部辅助 ----


def _scan_and_upsert(db: Session, course_id: str) -> Material:
    scan = storage.scan_course_dir(course_id)
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        material = Material(course_id=course_id, dir_path=str(storage._course_dir(course_id)))
        db.add(material)
        db.commit()
    _rescan_material(db, material)
    return material


def _rescan_material(db: Session, material: Material) -> None:
    """重新扫描单课程：识别三件套 + srt转vtt + 课件提取 + 状态判定。"""
    scan = storage.scan_course_dir(material.course_id)
    material.video_path = scan["video_path"]
    material.subtitle_path = scan["subtitle_path"]
    material.subtitle_source_format = scan["subtitle_ext"]
    material.courseware_path = scan["courseware_path"]
    material.courseware_format = scan["courseware_ext"]

    errors: list[str] = []

    # 无视频 = 直接判 error
    if material.video_path is None:
        material.status = "error"
        material.error_message = "缺少视频文件"
    else:
        material.status = "ready"
        material.error_message = None

    # 字幕处理：srt 转 vtt；无字幕则待 Whisper 生成
    if material.subtitle_path:
        sub_path = Path(material.subtitle_path)
        if material.subtitle_source_format == "srt":
            srt_text = sub_path.read_text(encoding="utf-8", errors="ignore")
            vtt_text = srt_to_vtt(srt_text)
            vtt_path = sub_path.with_suffix(".vtt")
            vtt_path.write_text(vtt_text, encoding="utf-8")
            material.subtitle_path = str(vtt_path)
            material.subtitle_source_format = "vtt"
        material.subtitle_status = "ready"
        material.subtitle_source = "manual"
        material.subtitle_error = None
    else:
        material.subtitle_status = "pending"
        material.subtitle_source = None
        material.subtitle_error = None

    # 课件提取
    if material.courseware_path:
        cw_path = Path(material.courseware_path)
        try:
            text, has_chapters, warning = extract_courseware(cw_path, material.courseware_format or "")
            material.courseware_text_cached = text
            material.courseware_has_chapters = has_chapters
            if warning:
                errors.append(warning)
        except Exception as e:  # noqa: BLE001
            material.courseware_text_cached = None
            errors.append(f"课件提取失败: {e}")
    else:
        material.courseware_text_cached = None
        material.courseware_has_chapters = False

    if errors:
        material.error_message = "; ".join(errors)

    material.scanned_at = datetime.now(timezone.utc)
    db.add(material)
    db.commit()
