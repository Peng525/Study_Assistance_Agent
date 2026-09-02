"""素材文件存储：类型校验、上传保存、扫描内核。

方案 A（管理台上传）为主流程；scan_course_dir 作为方案 B 备选共用内核。
"""

import shutil
import uuid
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings

# 每种文件类型：允许扩展名、大小上限（字节）、目录内命名
FILE_TYPES = {
    "video": {"exts": {".mp4", ".webm"}, "max_bytes": 2 * 1024 * 1024 * 1024, "label": "视频"},
    "subtitle": {"exts": {".vtt", ".srt"}, "max_bytes": 10 * 1024 * 1024, "label": "字幕"},
    "courseware": {"exts": {".md", ".pdf", ".pptx"}, "max_bytes": 50 * 1024 * 1024, "label": "课件"},
}

# magic number 校验（文件头字节）
_MAGIC_SIGNATURES = {
    ".mp4": [(4, b"ftyp")],
    ".webm": [(0, b"\x1a\x45\xdf\xa3")],
    ".vtt": [(0, b"WEBVTT")],
    ".pdf": [(0, b"%PDF")],
    ".pptx": [(0, b"PK\x03\x04")],
}


def _materials_root() -> Path:
    root = Path(settings.materials_dir)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root


def _course_dir(course_id: str) -> Path:
    return _materials_root() / course_id


def validate_filename(filename: str) -> str | None:
    """校验文件名是否安全（防路径穿越），返回错误文案或 None。"""
    if ".." in filename or "/" in filename or "\\" in filename:
        return "非法文件名"
    return None


def validate_course_id(course_id: str) -> str | None:
    """课程 ID 同时用于目录名，禁止空值和路径片段。"""
    if not course_id.strip() or len(course_id) > 128:
        return "课程 ID 不能为空且不能超过 128 个字符"
    if ".." in course_id or "/" in course_id or "\\" in course_id:
        return "课程 ID 不能包含路径字符"
    return None


def validate_extension(file_type: str, filename: str) -> str | None:
    """校验扩展名是否在白名单，返回错误文案或 None。"""
    ext = Path(filename).suffix.lower()
    cfg = FILE_TYPES.get(file_type)
    if cfg is None:
        return "未知文件类型"
    if ext not in cfg["exts"]:
        allowed = "/".join(sorted(cfg["exts"]))
        return f"仅支持 {allowed} 格式"
    return ext


def validate_magic(file_type: str, ext: str, head: bytes) -> str | None:
    """校验文件头 magic number，返回错误文案或 None。"""
    sigs = _MAGIC_SIGNATURES.get(ext)
    if sigs is None:
        return None  # 无 magic 规则的格式（srt/md 纯文本）跳过
    for offset, sig in sigs:
        if len(head) >= offset + len(sig) and head[offset : offset + len(sig)] == sig:
            return None
    return "文件内容与扩展名不符"


def save_upload(course_id: str, file_type: str, original_filename: str, content: bytes) -> Path:
    """保存上传文件（内存版，用于小文件/测试），返回磁盘路径。覆盖同类型旧文件。"""
    ext = Path(original_filename).suffix.lower()
    course_dir = _course_dir(course_id)
    course_dir.mkdir(parents=True, exist_ok=True)
    _remove_type_files(course_dir, file_type, ext)
    stored_name = f"{file_type}_{uuid.uuid4().hex[:8]}{ext}"
    dest = course_dir / stored_name
    dest.write_bytes(content)
    return dest


def target_path(course_id: str, file_type: str, original_filename: str) -> Path:
    """生成上传目标路径（uuid 重命名，不落盘），并预清理同类型旧文件。"""
    ext = Path(original_filename).suffix.lower()
    course_dir = _course_dir(course_id)
    course_dir.mkdir(parents=True, exist_ok=True)
    _remove_type_files(course_dir, file_type, ext)
    stored_name = f"{file_type}_{uuid.uuid4().hex[:8]}{ext}"
    return course_dir / stored_name


def _remove_type_files(course_dir: Path, file_type: str, ext: str) -> None:
    """删除课程目录下同类型旧文件（覆盖语义，避免文件累积）。"""
    for f in course_dir.iterdir():
        if f.is_file() and f.name.startswith(f"{file_type}_") and f.suffix.lower() == ext:
            f.unlink(missing_ok=True)


def list_course_files(course_id: str) -> list[dict]:
    """列出课程目录下已上传文件。"""
    course_dir = _course_dir(course_id)
    if not course_dir.exists():
        return []
    result = []
    for f in sorted(course_dir.iterdir()):
        if f.is_file():
            result.append(
                {
                    "filename": f.name,
                    "size": f.stat().st_size,
                    "uploaded_at": f.stat().st_mtime,
                }
            )
    return result


def scan_course_dir(course_id: str) -> dict:
    """扫描课程目录，识别视频/字幕/课件三件套。

    返回 {video_path, subtitle_path, subtitle_ext, courseware_path, courseware_ext}
    缺哪项哪项为 None。
    """
    course_dir = _course_dir(course_id)
    result = {
        "video_path": None,
        "subtitle_path": None,
        "subtitle_ext": None,
        "courseware_path": None,
        "courseware_ext": None,
    }
    if not course_dir.exists():
        return result

    video_exts = FILE_TYPES["video"]["exts"]
    subtitle_exts = FILE_TYPES["subtitle"]["exts"]
    courseware_exts = FILE_TYPES["courseware"]["exts"]

    for f in course_dir.iterdir():
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in video_exts and result["video_path"] is None:
            result["video_path"] = str(f)
        elif ext in subtitle_exts and result["subtitle_path"] is None:
            result["subtitle_path"] = str(f)
            result["subtitle_ext"] = ext.lstrip(".")
        elif ext in courseware_exts and result["courseware_path"] is None:
            result["courseware_path"] = str(f)
            result["courseware_ext"] = ext.lstrip(".")
    return result


def delete_course_file(course_id: str, file_type: str) -> bool:
    """删除课程目录下某类文件，返回是否删除成功。"""
    scan = scan_course_dir(course_id)
    key_map = {
        "video": "video_path",
        "subtitle": "subtitle_path",
        "courseware": "courseware_path",
    }
    key = key_map.get(file_type)
    if key is None:
        return False
    path_str = scan.get(key)
    if path_str is None:
        return False
    p = Path(path_str)
    if p.exists():
        p.unlink()
    return True
