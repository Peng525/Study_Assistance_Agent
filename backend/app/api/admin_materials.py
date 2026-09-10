"""管理台素材管理接口（上传/文件列表/删除/扫描/rescan）。"""

import hashlib
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.models import ContentSeries, Material, ProjectSource, User, VideoKnowledge
from app.services import storage
from app.services import whisper_service
from app.services.courseware import extract_courseware
from app.services.project_context import (
    bind_material,
    current_series_source,
    ensure_default_project,
    ensure_video_knowledge,
)
from app.services.context_builder import parse_vtt_cues
from app.services.subtitle import cue_revision, cues_to_vtt, detect_unsupported_format, srt_to_vtt, validate_cues

router = APIRouter(prefix="/api/admin/materials", tags=["admin-materials"])

logger = logging.getLogger(__name__)

# 字幕写回的进程内串行锁。PRD §5.5A.6：
# `revision` 校验与文件写入构成 check-then-act，两个请求同时读到同一个 revision
# 就会双双通过校验、后写的覆盖先写的，而双方都收到「保存成功」。
# 把「读文件 → 算 revision → 校验 → 写 tmp → replace」整段包进临界区，
# 使 check-then-act 成为原子操作。跨进程靠唯一 tmp 名 + 原子 replace 兜底。
_CUES_WRITE_LOCK = threading.Lock()


class SubtitleReviewRequest(BaseModel):
    """管理员审核字幕：标记 reviewed 解锁自动证据，或回退 unreviewed。"""

    review_state: str  # 'reviewed' | 'unreviewed'


class SubtitleCuesRequest(BaseModel):
    """保存编辑后的字幕 cues（P4 编辑器写回）。revision 为乐观锁指纹。"""

    cues: list[dict]
    revision: str  # 必须等于当前 VTT 的 sha1[:8]，否则 409 冲突


# ---- 批量操作（v8 §5.5A.5）----
#
# 状态准入白名单：前端按这份白名单过滤要提交的 course_ids，后端用**同一份**白名单二次校验。
# 后端不能只信前端 —— 即使收到 ready 的 ID 调"生成字幕"，也必须返回该条 ok=false，
# 而不是把它重复塞进队列（那会让一个 ready 的字幕被重新转写，且 review_state 语义被破坏）。
GENERATABLE = ("pending", "error")   # 仅待生成 / 生成失败可重新生成
# ⚠️ 只含 "generating"：DB 的 `pending` 表示**从未排上任务**（无视频 / 缺 ffmpeg / 取消后 / 孤儿复位），
# 内存里根本没有对应的 TaskState，谈不上"取消"。旧版把它列进来，靠 runtime 的 peek 兜底，
# 结果是准入校验与真实可取消性用了两套标准。现在两者统一：进不了白名单 = 不可取消。
CANCELLABLE = ("generating",)
REVIEWABLE = ("ready",)              # 仅已生成可审核


class BatchIdsRequest(BaseModel):
    course_ids: list[str]


class BatchReviewRequest(BaseModel):
    course_ids: list[str]
    review_state: str  # 'reviewed' | 'unreviewed'


class BatchItemResult(BaseModel):
    course_id: str
    ok: bool
    status: str | None = None   # 操作后的 subtitle_status
    error: str | None = None


class BatchResponse(BaseModel):
    """best-effort 批量结果：HTTP 恒 200，成败体现在每条的 ok/error 上。"""

    succeeded: int
    failed: int
    results: list[BatchItemResult]

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
    course_type: str = "theory",
    source_id: int | None = None,
    series_id: int | None = None,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if file_type not in storage.FILE_TYPES:
        raise HTTPException(status_code=400, detail="未知文件类型")
    if file_type == "video" and course_type not in {"theory", "practice"}:
        raise HTTPException(status_code=400, detail="课程类型仅支持 theory 或 practice")
    selected_source = None
    selected_series = None
    if file_type == "video" and series_id is not None:
        project = ensure_default_project(db)
        selected_series = db.query(ContentSeries).filter(
            ContentSeries.id == series_id,
            ContentSeries.project_id == project.id,
        ).first()
        if selected_series is None:
            raise HTTPException(status_code=400, detail="所选专栏不存在")
        selected_source = current_series_source(db, selected_series.id)
    if file_type == "video" and source_id is not None:
        project = ensure_default_project(db)
        legacy_source = db.query(ProjectSource).filter(
            ProjectSource.id == source_id,
            ProjectSource.project_id == project.id,
            ProjectSource.status == "active",
            ProjectSource.source_format == "pptx",
        ).first()
        if legacy_source is None or legacy_source.series_id is None:
            raise HTTPException(status_code=400, detail="所选 PPT 专栏不存在或已失效")
        if selected_series is not None and selected_series.id != legacy_source.series_id:
            raise HTTPException(status_code=400, detail="series_id 与 source_id 不属于同一专栏")
        selected_series = db.get(ContentSeries, legacy_source.series_id)
        selected_source = legacy_source
    course_id_error = storage.validate_course_id(course_id)
    if course_id_error:
        raise HTTPException(status_code=400, detail=course_id_error)

    existing_material = db.query(Material).filter(Material.course_id == course_id).first()
    existing_knowledge = (
        db.query(VideoKnowledge).filter(VideoKnowledge.material_id == existing_material.id).first()
        if existing_material else None
    )
    if (
        file_type == "video"
        and selected_series is not None
        and existing_knowledge is not None
        and existing_knowledge.series_id not in {None, selected_series.id}
    ):
        raise HTTPException(status_code=409, detail="视频已经归入其他专栏，不能移动")

    original_filename = file.filename or ""
    err = storage.validate_filename(original_filename)
    if err:
        raise HTTPException(status_code=400, detail=err)

    cfg = storage.FILE_TYPES[file_type]
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if f".{ext}" not in cfg["exts"]:
        allowed = "/".join(sorted(cfg["exts"]))
        raise HTTPException(status_code=400, detail=f"仅支持 {allowed} 格式")

    # 流式写入临时文件，避免一次性 read 全量进内存（大视频风险）
    dest = storage.target_path(course_id, file_type, original_filename)
    tmp_path = dest.with_suffix(dest.suffix + ".part")
    max_bytes = cfg["max_bytes"]
    head = b""
    total = 0
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB 分块
                if not chunk:
                    break
                if not head:
                    head = chunk[:16]
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{cfg['label']}文件过大，上限 {max_bytes // 1024 // 1024}MB",
                    )
                out.write(chunk)

        # magic number 校验
        if storage.validate_magic(file_type, f".{ext}", head):
            raise HTTPException(status_code=400, detail="文件内容与扩展名不符")

        # 字幕格式额外校验（只读前 4KB）
        if file_type == "subtitle":
            with open(tmp_path, "rb") as f:
                sample = f.read(4096).decode("utf-8", errors="ignore")
            unsupported = detect_unsupported_format(sample)
            if unsupported:
                raise HTTPException(status_code=400, detail=unsupported)

        # 校验通过，move 到目标
        tmp_path.replace(dest)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

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
    if file_type == "video":
        bind_material(db, material, course_type=course_type)
        if selected_source is not None:
            knowledge = ensure_video_knowledge(db, material, course_type)
            if knowledge.series_id is None:
                knowledge.series_id = selected_series.id
            if knowledge.source_id != selected_source.id:
                knowledge.source_id = selected_source.id
                knowledge.page_start = None
                knowledge.page_end = None
                knowledge.knowledge_text_cached = None
                knowledge.knowledge_text_path = None
                knowledge.outline_text_cached = None
                knowledge.outline_text_path = None
                knowledge.outline_status = "empty"
                db.add(knowledge)
        elif selected_series is not None:
            knowledge = ensure_video_knowledge(db, material, course_type)
            if knowledge.series_id is None:
                knowledge.series_id = selected_series.id
                db.add(knowledge)
        db.commit()

    return {
        "message": f"{cfg['label']}上传成功",
        "filename": original_filename,
        "path": str(dest),
        "course_id": course_id,
        "course_type": course_type if file_type == "video" else None,
        "source_id": selected_source.id if selected_source else None,
        "series_id": selected_series.id if selected_series else None,
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


# ---- 批量端点（v8 §5.5A.5）----
#
# ⚠️ 路由注册顺序：这三个 /batch/* **必须注册在 /{course_id}/* 之前**。
# FastAPI 按注册顺序匹配，否则 "/batch/generate-subtitle" 会被
# "/{course_id}/generate-subtitle" 当成 course_id="batch" 命中，返回 404「课程不存在」。
# 回归闸门见 tests/test_admin_materials_batch.py::test_batch_route_not_swallowed_by_course_id


@router.post("/batch/generate-subtitle", response_model=BatchResponse)
def batch_generate_subtitle(
    body: BatchIdsRequest,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量生成字幕。准入：subtitle_status ∈ {pending, error}。

    传入其它状态的 course_id 时，该条返回 ok=false + 原因，**不会重复入队**——
    后端用自己的白名单二次校验，不依赖前端过滤。
    ffmpeg 整批只探测一次（每次调用都要 shutil.which，且可能 import imageio_ffmpeg）。
    """
    if not whisper_service.is_ffmpeg_available():
        raise HTTPException(
            status_code=400,
            detail="未检测到 ffmpeg，请先安装并加入 PATH（Whisper 依赖）",
        )
    return _batch_response(_run_batch(db, body.course_ids, _do_generate_subtitle))


@router.post("/batch/cancel-subtitle", response_model=BatchResponse)
def batch_cancel_subtitle(
    body: BatchIdsRequest,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量取消生成。准入：subtitle_status ∈ {pending, generating}。"""
    return _batch_response(_run_batch(db, body.course_ids, _do_cancel_subtitle))


@router.post("/batch/review", response_model=BatchResponse)
def batch_review(
    body: BatchReviewRequest,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量标记 / 撤销审核。准入：subtitle_status == ready。

    `review_state` 非法属于入参错误（不是 per-item 失败），整体 400 拒绝、DB 不改。
    """
    if body.review_state not in ("reviewed", "unreviewed"):
        raise HTTPException(status_code=400, detail="review_state 仅支持 reviewed / unreviewed")
    return _batch_response(
        _run_batch(db, body.course_ids, lambda m: _do_review_subtitle(m, body.review_state))
    )


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
    """手动触发 Whisper 字幕生成（失败后重试）。

    **v8 修复的 root-cause**：以前用 `enqueue()` 的返回值决定 DB 写 pending 还是 generating，
    而 enqueue() 对新任务恒返回 PENDING（worker 此时还没启动）→ DB 永远停在 pending
    → 前端轮询不启动、进度条不渲染、按钮态不变化，整条进度链路形同虚设。
    现在入队成功即无条件写 generating，与 `_rescan_material()` 的扫描路径保持一致。
    """
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
    try:
        _do_generate_subtitle(material)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    db.commit()
    return {
        "course_id": course_id,
        "subtitle_status": material.subtitle_status,
        # get_status() 已删除（它会为陌生 course_id 凭空造 TaskState）。
        # 这里刚 enqueue 过，任务必然存在；仍用 `or zero_snapshot()` 兜底是防御性写法，
        # 保证端点在任何情况下都不会 500。
        "queue_position": (
            whisper_service.peek_status(course_id) or whisper_service.zero_snapshot()
        )["queue_position"],
    }


@router.post("/{course_id}/cancel-subtitle")
def cancel_subtitle(
    course_id: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """取消生成任务：排队中（pending）或生成中（generating）均可取消。"""
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    try:
        _do_cancel_subtitle(material)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    db.commit()
    return {"message": "已取消"}


@router.post("/{course_id}/subtitle/review")
def review_subtitle(
    course_id: str,
    body: SubtitleReviewRequest,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """标记字幕审核状态：reviewed 解锁自动 Transcript Context 注入；unreviewed 回退。

    ⚠️ 措辞纪律：不要用「生成即生效」。生成完成（ready）只代表允许展示/主动引用，
    未审核（unreviewed）前不得自动作为 AI 证据。
    """
    if body.review_state not in ("reviewed", "unreviewed"):
        raise HTTPException(status_code=400, detail="review_state 仅支持 reviewed / unreviewed")
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    # 只有真实生成/上传好（ready）的字幕才有审核意义；generating/pending/error 不让标记。
    try:
        _do_review_subtitle(material, body.review_state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    db.commit()
    return {
        "course_id": course_id,
        "subtitle_status": material.subtitle_status,
        "review_state": material.review_state,
    }


@router.get("/{course_id}/subtitle/cues")
def get_subtitle_cues(
    course_id: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """读取当前字幕的 cues（供编辑器/P5 预览加载）。返回 cues + revision 乐观锁指纹。"""
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    if not material.subtitle_path or not Path(material.subtitle_path).exists():
        raise HTTPException(status_code=404, detail="字幕文件不存在")
    text = Path(material.subtitle_path).read_text(encoding="utf-8")
    return {
        "course_id": course_id,
        "subtitle_status": material.subtitle_status,
        "review_state": material.review_state,
        "revision": cue_revision(text),
        "cues": parse_vtt_cues(text),
    }


@router.put("/{course_id}/subtitle/cues")
def save_subtitle_cues(
    course_id: str,
    body: SubtitleCuesRequest,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """保存编辑后的字幕 cues（P4 编辑器写回）。

    - 乐观锁：body.revision 必须等于当前 VTT 的 sha1[:8]，否则 409 冲突（前端重新拉取再保存）；
    - 时间轴校验（前后端都做）：非法时间轴会让播放器崩溃，校验失败 400；
    - 编辑使旧审核失效：review_state 复位 unreviewed（改过的字幕需重新人工抽查）。
    """
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    path = Path(material.subtitle_path) if material.subtitle_path else None
    if path is None or not path.exists():
        raise HTTPException(status_code=400, detail="字幕文件不存在，无法编辑")

    # 纯内存的校验/转换放锁外，别把锁的持有时间拖长
    err = validate_cues(body.cues)
    if err:
        raise HTTPException(status_code=400, detail=err)
    new_vtt = cues_to_vtt(body.cues)

    with _CUES_WRITE_LOCK:
        # ⚠️ revision 必须在**锁内**重算。锁外算出来的 revision，等到真正拿到锁时
        # 文件可能已被别的请求改写 —— 那时校验通过的是一个过期的指纹，形同虚设。
        text = path.read_text(encoding="utf-8")
        current_rev = cue_revision(text)
        if body.revision != current_rev:
            raise HTTPException(
                status_code=409,
                detail=f"字幕已被改动（revision 不匹配，当前 {current_rev}），请重新拉取后再保存",
            )
        # tmp 名唯一化：固定名 `xxx.vtt.tmp` 会让并发请求写同一个文件，
        # replace 的原子性只保证单次 swap，不保证 swap 进去的内容是谁写的。
        tmp = path.with_name(f"{path.name}.{uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(new_vtt, encoding="utf-8")
            tmp.replace(path)  # 原子写回
        except Exception:
            tmp.unlink(missing_ok=True)  # 别留垃圾 tmp 污染下一次扫描
            raise

    material.review_state = "unreviewed"  # 编辑使旧审核失效
    db.commit()
    return {
        "course_id": course_id,
        "subtitle_status": material.subtitle_status,
        "review_state": material.review_state,
        "revision": cue_revision(new_vtt),
    }


@router.get("/whisper/model-status")
def whisper_model_status(current: User = Depends(require_admin)):
    """返回**实际**的推理配置与 GPU 可用性。

    ⚠️ 这里曾经把 `"model"` 硬编码成 `"medium"`（真实配置是 `small`），
    排查模型问题时直接把人带偏。现在一律走 `whisper_service.describe_runtime()`，
    设备/精度由配置真实解析得出，并附带 GPU 不可用的具体原因。
    """
    return whisper_service.describe_runtime()


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
    # D2：先记下**扫描前**的字幕内容与审核结论，用来判断"这次扫描是否实质改变了字幕"。
    # 必须在下面 `material.subtitle_path = scan[...]` 覆盖之前取。
    #
    # ⚠️ 已知边界（诚实记录，不要当成 bug 去"修"）：指纹是**实时读文件**算的，
    # 所以只有"字幕文件路径变了"才能察觉内容变化。
    # 若管理员用新内容**覆盖了同名文件**，扫描时读到的就已经是新内容，
    # prev 与 new 必然相等 —— 这种情况扫不出来，靠"编辑保存"路径
    # （`PUT /subtitle/cues` 无条件写 unreviewed）兜底。
    # 要真正覆盖到同名覆盖场景，得把指纹持久化成一列（不在本轮范围）。
    prev_subtitle_fp = _subtitle_fingerprint(material.subtitle_path)
    prev_reviewed = material.review_state == "reviewed"
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

    # 字幕处理：srt 转 vtt；无字幕时只同步元数据，不触发生成（生成由 admin 手动点）
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
        # ⚠️ 保留已有来源，**不得按文件名覆盖**（PRD §5.5A.7 / AC-14）。
        # 「扫描」是读取磁盘事实，不是"字幕从哪来"这件事的发生点；
        # 旧实现无条件写 `_infer_subtitle_source(path)`，一次重扫就把全部
        # 机器生成的字幕改标成「人工上传」，管理员会以为人已校过而跳过抽查。
        # `_infer_subtitle_source()` 降级为 **legacy fallback**：仅在来源缺失时兜底。
        if material.subtitle_source is None:
            material.subtitle_source = _infer_subtitle_source(material.subtitle_path)
        material.subtitle_error = None
        # D2：只有字幕内容**真的变了**才复位审核结论。
        # 用内容指纹而非 mtime：文件被复制/恢复/打包解包时 mtime 会无意义地变化，
        # 让管理员"只是扫了一下"就丢掉全部审核结论。
        if prev_reviewed and _subtitle_fingerprint(material.subtitle_path) != prev_subtitle_fp:
            material.review_state = "unreviewed"
    else:
        # 无字幕文件：rescan 只重新识别素材，**不再自动排字幕任务**
        # （PRD §5.5A.6：Whisper 失败不自动重试，由 admin 手动触发）。
        #
        # ⚠️ 旧实现在这里无条件 `enqueue()` + 写 `generating` + `source="whisper"`：
        # 管理员"只是点了重新扫描素材"，3 条失败任务就被重新塞回队列；
        # 而且不论之前是什么状态都先洗成 `pending` + `subtitle_error=None`，
        # 把 error + "File model.bin is incomplete..." 这类真实失败现场抹掉，
        # 事后无从定位。
        #
        # 现在按**扫描前的状态**分派：
        #   error                    → 保持 error，保留 subtitle_error（留住故障现场）
        #   generating + 无 runtime  → 不写，交给 D3 orphan 自愈（下次
        #                              GET /api/materials 幂等恢复 pending 并打
        #                              [subtitle-orphan] 日志）。这里若自己写
        #                              pending，D3 就永远检测不到 orphan 了。
        #   pending / ready+文件缺失 → 沿用既定边界：pending
        #
        # 不动 subtitle_source：没有字幕就不存在"来源"这回事，留着上次的值
        # 反而能在字幕文件被误删后追溯它原本是哪来的。
        prev_status = material.subtitle_status
        if prev_status == "error":
            pass  # 留住故障现场：admin 要能看到"上次为什么失败"
        elif prev_status == "generating" and not whisper_service.task_is_active(material.course_id):
            pass  # orphan，交给 D3 自愈，不在这里抢
        else:
            material.subtitle_status = "pending"
            material.subtitle_error = None

        # ffmpeg 缺失是**环境问题**，提示管理员；但不覆盖 error 的真实失败记录。
        if material.video_path and not whisper_service.is_ffmpeg_available():
            if material.subtitle_status != "error":
                material.subtitle_error = "未检测到 ffmpeg，无法自动生成字幕，请安装后手动触发"
        logger.info(
            "[rescan] %s 无字幕文件（扫描前状态 %s → 现在 %s），如需生成请 admin 手动点击生成字幕",
            material.course_id, prev_status, material.subtitle_status,
        )
        # 字幕没了，旧的审核结论无从谈起
        if prev_reviewed:
            material.review_state = "unreviewed"

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
    db.flush()
    bind_material(db, material)
    db.commit()


# ---- 字幕状态准入校验（v8 §5.5A.5：单条端点与批量端点共用同一套规则）----


def _do_generate_subtitle(material: Material) -> None:
    """单条生成。违反准入条件时抛 ValueError(msg)，由调用方转成该条的 error。"""
    if material.video_path is None:
        raise ValueError("该课程无视频文件")
    if material.subtitle_status not in GENERATABLE:
        raise ValueError(
            f"当前状态 {material.subtitle_status} 不可生成字幕（仅 {'/'.join(GENERATABLE)} 可）"
        )
    # ⚠️ 不要用 enqueue() 的返回值决定 DB 写什么 —— 新任务入队后 worker 尚未启动，
    # enqueue() 恒返回 PENDING。DB 必须无条件写 generating，否则前端轮询不启动、
    # 进度条不渲染、按钮态不变化（v8 修复的 root-cause，PRD §5.5A.3）。
    whisper_service.enqueue(material.course_id, material.video_path)
    material.subtitle_status = "generating"
    material.subtitle_source = "whisper"
    material.subtitle_error = None   # 重试时清掉上一次的失败文案
    # 重新生成意味着"这一版字幕还没被看过"，旧审核结论必须作废。
    # 不复位的话，新生成的机器转写会顶着上一版的「已审核」直接获得
    # 自动作为 Transcript Context 的资格 —— 未校对的转写混进上下文会污染答案。
    material.review_state = "unreviewed"


# Whisper 落盘命名（与 whisper_service._run_whisper 的 `<视频主名>.whisper.vtt` 严格对应）。
# 人工上传的字幕由 storage 重命名为 `subtitle_<uuid>.vtt`，或保留用户自放的文件名。
_WHISPER_VTT_SUFFIX = ".whisper.vtt"


def _subtitle_fingerprint(subtitle_path: str | None) -> str | None:
    """字幕**内容**指纹（D2 的判定依据）。文件不存在/解析失败/无 cue 时返回 None。

    基于**解析后的 cue 文本 + 时间轴**，不是文件字节，也不是 mtime：

    - 不用字节：`srt → vtt` 转换会重写整个文件，字节全变但内容一字未改。
      用字节的话，扫描一次就把审核结论冲掉 —— 正是 D2 要避免的。
    - 不用 mtime：文件被复制 / 恢复 / 打包解包时 mtime 会无意义地变化。
      管理员"只是扫了一下素材"不该丢掉审核结论（PRD §5.5A.7）。
    """
    if not subtitle_path:
        return None
    path = Path(subtitle_path)
    if not path.is_file():
        return None
    try:
        cues = parse_vtt_cues(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:  # noqa: BLE001 — 坏字幕不该让扫描整个失败
        return None
    if not cues:
        return None
    payload = "\n".join(
        f"{c.get('start')}|{c.get('end')}|{(c.get('text') or '').strip()}" for c in cues
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _infer_subtitle_source(subtitle_path: str | None) -> str:
    """按文件名判定字幕来源（PRD §5.5A.6 Drawer「来源」列的唯一依据）。

    ⚠️ **只能作为 legacy fallback**：仅在 `subtitle_source is None`（老数据从未写过来源）时调用，
    **不得覆盖数据库里已有的 provenance**。扫描是读取磁盘事实，不是"字幕从哪来"这件事的发生点。

    ⚠️ 判据**偏向标成 whisper**：把 AI 转写误标成「人工上传」时，管理员会以为
    已经有人校过而跳过抽查直接解锁自动证据注入 —— 未校对的机器转写混进上下文
    会直接污染答案，这是本项目最核心的卖点。反向误标只是显示不准，无安全风险。
    """
    if not subtitle_path:
        return "whisper"
    return "whisper" if Path(subtitle_path).name.endswith(_WHISPER_VTT_SUFFIX) else "manual"


def _do_cancel_subtitle(material: Material) -> None:
    """取消生成任务。失败路径一律抛 ValueError（调用方转成该条 error / HTTP 400）。

    **取消成功后 DB 回到 `pending` + `subtitle_error = None`** ——
    取消是"这次没生成、随时可以再来"，不是"这个素材生成不了"。
    写 `error` 会让管理员误判素材有问题（PRD §5.5A.3「取消不是业务状态」）。
    """
    if material.subtitle_status not in CANCELLABLE:
        raise ValueError(
            f"当前状态 {material.subtitle_status} 不可取消（仅 {'/'.join(CANCELLABLE)} 可）"
        )
    # ⚠️ 必须是 peek_status()：get_status() 会为陌生 course_id 凭空造一个 PENDING TaskState，
    # 导致「从未排过队的 generating 行」也能通过下面的检查，取消返回成功却什么都没取消。
    # get_status() 现已删除，只留无副作用的 peek_status() —— 不存在"会造状态"的读取函数。
    #
    # 这一道检查不是冗余的白名单复查：DB 的 `generating` 只是一个**意图**，
    # 进程重启后它可能指向一个根本不存在的任务（orphan）。
    # 没有 runtime 任务就没有东西可取消 —— 报告失败，而不是假装成功。
    outcome = whisper_service.cancel(material.course_id)
    if outcome is None:
        raise ValueError("队列中无该任务")

    if outcome == "queued":
        # ⚠️ 排队中的任务必须**由取消请求自己收尾**：
        # cancel() 只把 course_id 移出 _queue 并清掉 TaskState，而 _worker_loop
        # 只处理从 _queue[0] 取到的 course_id —— 已出队的任务永远不会被 worker 碰，
        # 也就没有任何人会调用 _write_back_to_db。
        # 不在这里收尾的话，DB 会永远停在 generating，前端一直显示"生成中"并空轮询，
        # 直到进程重启被 seed._reconcile_orphan_subtitle_tasks 复位为止。
        material.subtitle_status = "pending"
        material.subtitle_error = None
    # outcome == "generating"：worker 在 7 个检测点响应 _cancel_requested 后抛
    # _CancelledError，由 _write_back_to_db(outcome="cancelled") 自己写回 pending。
    # 这里**不预写 DB** —— 预写会让"生成中"的行在 worker 真正收尾前先闪成"待生成"，
    # 与仍在跑的任务自相矛盾。


def _do_review_subtitle(material: Material, review_state: str) -> None:
    if material.subtitle_status not in REVIEWABLE:
        raise ValueError("字幕尚未生成完成，无法审核（仅 ready 可审核）")
    material.review_state = review_state


def _run_batch(db: Session, course_ids: list[str], fn) -> list[BatchItemResult]:
    """best-effort 批量执行：逐条 try/except 收集结果，**每条立即 commit**。

    两条硬约束（PRD §5.5A.5）：

    1. **每一条无论成败都必须出现在 `results` 里** → 捕获 `Exception`，不能只捕获 `ValueError`。
       IO 错误、DB 错误、`HTTPException` 都会让循环中断，剩余 ID 既不执行也不出现在结果里，
       且返回 HTTP 500，与「HTTP 200 + per-item」的契约直接冲突。
    2. **每条立即 commit，不攒到末尾**。`enqueue()` 会起后台线程，
       **线程已启动就 rollback 不回来**，所谓"事务原子"是假的。攒到末尾时若中途异常，
       已 enqueue 的线程仍会用独立 SessionLocal 把 DB 写回 `ready` ——
       结果是「HTTP 说失败、字幕最后却变成 ready」，管理员会重复点生成。

    单条失败后必须 `db.rollback()`：SQLAlchemy session 在异常后处于脏状态，
    不回滚会让后续所有查询抛 `PendingRollbackError`，把"一条失败"放大成"整批失败"。
    """
    results: list[BatchItemResult] = []
    for course_id in course_ids:
        material = db.query(Material).filter(Material.course_id == course_id).first()
        if material is None:
            results.append(BatchItemResult(course_id=course_id, ok=False, error="课程不存在"))
            continue
        try:
            fn(material)
            # commit 会让 material 过期（expire_on_commit），先取值再提交
            status = material.subtitle_status
            db.commit()
            results.append(BatchItemResult(course_id=course_id, ok=True, status=status))
        except Exception as e:  # noqa: BLE001 — best-effort：任何异常都不能中断循环
            db.rollback()  # 只回滚这一条自己，不能让它污染后续条目
            results.append(
                BatchItemResult(course_id=course_id, ok=False, error=str(e) or type(e).__name__)
            )
    return results


def _batch_response(results: list[BatchItemResult]) -> BatchResponse:
    return BatchResponse(
        succeeded=sum(1 for r in results if r.ok),
        failed=sum(1 for r in results if not r.ok),
        results=results,
    )
