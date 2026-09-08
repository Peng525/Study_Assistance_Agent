"""素材公开接口（课程列表/视频流/字幕/字幕状态）。"""

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.config import settings
from app.core.security import create_media_ticket, decode_media_ticket
from app.models.models import Material, ProjectSource, User, VideoKnowledge
from app.services import storage
from app.services import whisper_service
from app.services.context_builder import parse_vtt_cues

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/materials", tags=["materials"])


class MaterialListItem(BaseModel):
    """素材列表项契约（v8 §5.5A）。

    字段命名分两组，避免前端误读：
      - `subtitle_*`（无前缀 task_）= DB 真值，持久可靠；
      - 运行态（`subtitle_progress` / `subtitle_slices_*` / `subtitle_phase` /
        `subtitle_started_at` / `subtitle_queue_position`）= 内存 worker 快照，进程重启即丢。
    """

    course_id: str
    status: str
    error_message: str | None = None
    courseware_format: str | None = None
    subtitle_status: str
    subtitle_source: str | None = None          # 'manual' | 'whisper'
    subtitle_filename: str | None = None        # 仅文件名，不含目录
    subtitle_relative_path: str | None = None   # 后端脱敏后的项目相对路径（见 _relative_subtitle_path）
    subtitle_error: str | None = None           # ⚠️ 仅 admin 可见（可能含内部路径/异常原文）
    # D4：字幕文件是否**真的在磁盘上**。由 `subtitle_path + Path.is_file()` 实时派生，
    # **不是数据库列**。`subtitle_filename` 只是历史记录，文件被手工删掉后它仍有值 ——
    # 用它判断存在性会给出"点开才报错"的假入口（PRD §5.5A.7 / AC-2）。
    subtitle_has_file: bool = False
    # D3：runtime 里是否存在**真实的未完成任务**（PENDING / GENERATING）。
    # DB 的 `generating` 只是一个"意图"，进程重启后它可能指向根本不存在的任务（orphan）。
    # 前端据此过滤「可取消的行」，否则会勾到一行永远取消不掉的素材（PRD §5.5A.5）。
    subtitle_task_active: bool = False
    subtitle_progress: float = 0.0
    subtitle_slices_done: int = 0
    subtitle_slices_total: int = 0
    subtitle_phase: str | None = None           # 'loading_model' | 'transcribing' | 'merging' | None
    subtitle_started_at: float | None = None    # Unix 时间戳；降级轨用
    subtitle_queue_position: int = 0
    review_state: str
    title: str | None = None
    duration: float | None = None
    course_type: str | None = None
    source_id: int | None = None
    source_filename: str | None = None
    scanned_at: str | None = None


class SubtitleStatusResponse(BaseModel):
    """字幕状态轮询响应（v8 §5.5A.3）。

    命名纪律：`subtitle_*` = DB 真值，`task_*` = 内存 worker 易失状态。
    原实现把内存的 status 直接 `**status` 展开，与 DB 的 subtitle_status 撞名，前端极易误读。
    """

    course_id: str
    subtitle_status: str        # DB：pending / generating / ready / error
    review_state: str
    task_status: str            # 内存 worker 状态
    task_progress: float
    task_slices_done: int
    task_slices_total: int
    task_phase: str | None
    task_started_at: float | None
    task_error: str | None
    queue_position: int


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


def _relative_subtitle_path(subtitle_path: str | None) -> str | None:
    """把 DB 里的绝对路径转成项目相对路径并脱敏（v8 §5.5A.7）。

    - 落在项目 materials/ 目录下 → `materials/<course_id>/<filename>`
    - 落在项目外（历史数据 / 异常） → 只返回文件名，**绝不泄露服务器目录结构**

    前端只原样显示这个字符串，不得自行拼接 —— 否则目录结构一变，前端展示就开始撒谎。
    """
    if not subtitle_path:
        return None
    try:
        root = storage._materials_root().resolve()
        rel = Path(subtitle_path).resolve().relative_to(root)
        # 前缀取 root.name（通常为 "materials"）而不是硬编码：
        # 若 settings.materials_dir 改了目录名，这里跟着变，不会给前端一个不存在的路径。
        return f"{root.name}/{rel.as_posix()}"
    except (ValueError, OSError):
        return Path(subtitle_path).name


def _peek_runtime(course_id: str, subtitle_status: str) -> dict:
    """只对"可能在跑"的行查内存状态，其余直接给零值（不碰 _tasks）。

    用 `peek_status()` 而非 `get_status()`：后者会为陌生 course_id 凭空造 TaskState，
    把 `active_task_count()` 灌水，导致 /whisper/model-status 的 active_tasks 不准。
    """
    zero = {
        "subtitle_progress": 0.0,
        "subtitle_slices_done": 0,
        "subtitle_slices_total": 0,
        "subtitle_phase": None,
        "subtitle_started_at": None,
        "subtitle_queue_position": 0,
    }
    if subtitle_status not in ("pending", "generating"):
        return zero
    snap = whisper_service.peek_status(course_id)
    if snap is None:
        return zero
    return {
        "subtitle_progress": round(float(snap["progress"] or 0.0), 4),
        "subtitle_slices_done": int(snap["slices_done"] or 0),
        "subtitle_slices_total": int(snap["slices_total"] or 0),
        "subtitle_phase": snap["phase"],
        "subtitle_started_at": snap["started_at"],
        "subtitle_queue_position": int(snap["queue_position"] or 0),
    }


def _reconcile_orphan_generating(db: Session, materials: list[Material]) -> list[str]:
    """D3 第二层自愈：把「DB 写着 generating，但 runtime 里根本没有任务」的行恢复为 pending。

    返回被修复的 course_id 列表（供审计日志）。**没有 orphan 时返回空且不碰 DB。**

    这是一个**非法状态**（典型成因：进程被杀、worker 崩了），7 个展示态里没有它的位置，
    也不允许为它新增第 8 态。正确做法是**修好它**，而不是展示它。

    ⚠️ 在 GET 里写库是刻意的取舍，因此必须满足四条硬约束（PRD §5.5A.3）：

    1. **只在 `generating` + 无真实 runtime 任务时触发**。
       不扩展到 `ready` + 文件缺失 —— 那个交给 rescan 修（见 D4），普通 GET 不产生写副作用。
    2. **幂等**：恢复为 `pending` 后，下一轮查询 `subtitle_status` 已不是 `generating`，不会再触发。
    3. **有审计日志**：记录 course_id，便于追溯"为什么这行自己变了"。
    4. **正常 GET 仍然是纯读**：没有 orphan 时一次写都不发生（由测试锁死，AC-17）。
    """
    orphan_ids: list[str] = []
    for m in materials:
        if m.subtitle_status != "generating":
            continue
        # worker 正在跑（PENDING=排队中 / GENERATING=转写中）→ 正常，不是 orphan
        if whisper_service.task_is_active(m.course_id):
            continue
        m.subtitle_status = "pending"
        m.subtitle_error = None
        orphan_ids.append(m.course_id)
    if orphan_ids:
        db.commit()
    return orphan_ids


@router.get("", response_model=list[MaterialListItem])
def list_materials(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    is_admin = current.role == "admin"
    query = db.query(Material)
    if current.role != "admin":
        query = query.filter(Material.status == "ready")  # user 只看 ready
    materials = query.order_by(Material.id).all()
    # D3：必须在任何派生之前自愈，否则下面算出的 subtitle_status / subtitle_task_active
    # 会基于一个已经不一致的旧值，前端就会看到「假 generating」并空轮询。
    orphan_ids = _reconcile_orphan_generating(db, materials)
    if orphan_ids:
        logger.warning(
            "[subtitle-orphan] 检测到 %d 条 DB=generating 但 runtime 无任务的素材，已幂等恢复为 pending：%s",
            len(orphan_ids),
            ", ".join(orphan_ids),
        )
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
            "subtitle_source": m.subtitle_source,
            "subtitle_filename": (
                m.subtitle_original_filename
                or (Path(m.subtitle_path).name if m.subtitle_path else None)
            ),
            "subtitle_relative_path": _relative_subtitle_path(m.subtitle_path),
            # ⚠️ subtitle_error 可能含服务器绝对路径与异常原文，只给 admin；
            #    user 角色该字段恒为 None（PRD §5.5A.7）。
            "subtitle_error": (m.subtitle_error if is_admin else None),
            # D4：实时 stat 磁盘。`subtitle_filename` 有值 ≠ 文件还在（可能被手工删了）。
            "subtitle_has_file": bool(m.subtitle_path) and Path(m.subtitle_path).is_file(),
            # D3/cancel：runtime 是否真有任务。前端用它过滤「可取消的行」。
            "subtitle_task_active": whisper_service.task_is_active(m.course_id),
            **_peek_runtime(m.course_id, m.subtitle_status),
            # A3：字幕审核状态（unreviewed/reviewed）。与 subtitle_status 正交，
            # 仅 ready+reviewed 才解锁自动 Transcript Context 注入。
            "review_state": m.review_state,
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
        "review_state": material.review_state,
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


@router.get("/{course_id}/subtitle-status", response_model=SubtitleStatusResponse)
def get_subtitle_status(course_id: str, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    is_admin = current.role == "admin"
    # ⚠️ peek 而非 get：get_status() 会为陌生 course_id 凭空造一个 PENDING TaskState，
    # 于是任何登录用户对任意 course_id 轮询一次，就往 _tasks 塞一条永不过期的记录，
    # active_task_count() 从此虚高、_tasks 无上限增长（v9 修复）。
    # 任务不存在时用零值快照兜底 —— 不造假，也不污染。
    status = whisper_service.peek_status(course_id) or whisper_service.zero_snapshot()
    return {
        "course_id": course_id,
        "subtitle_status": material.subtitle_status,
        "review_state": material.review_state,
        # 内存 worker 状态统一加 task_ 前缀，避免与 DB 的 subtitle_status 撞名（v8 §5.5A.3）
        "task_status": status["status"],
        "task_progress": status["progress"],
        "task_slices_done": status["slices_done"],
        "task_slices_total": status["slices_total"],
        "task_phase": status["phase"],
        "task_started_at": status["started_at"],
        # ⚠️ 与列表端点同一规则（PRD §5.5A.7）：task_error 是 worker 的 str(e)，
        # 常含服务器绝对路径与异常原文，**仅 admin 可见**。
        # 本端点只用 get_current_user 鉴权，原先漏了这层过滤。
        "task_error": status["error"] if is_admin else None,
        "queue_position": status["queue_position"],
    }
