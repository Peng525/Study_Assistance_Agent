"""管理台项目共享资料与版本化 Project Summary。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin_model_configs import get_default_config
from app.api.deps import require_admin
from app.core.database import get_db
from app.core.security import decrypt_api_key
from app.models.models import (
    Material,
    ProjectContextVersion,
    ProjectMaterial,
    ProjectSource,
    User,
    VideoKnowledge,
)
from app.services import storage
from app.services.courseware import extract_courseware
from app.services.llm_client import stream_chat
from app.services.model_router import RoutingOutcome, stream_model_chain
from app.services.project_context import (
    SUMMARY_SOURCE_TOKEN_LIMIT,
    active_sources,
    ensure_default_project,
    estimate_tokens,
    ensure_video_knowledge,
    latest_draft_version,
    latest_published_version,
    manifest_json,
    mark_published_stale,
    next_version_number,
    ppt_pages,
    select_ppt_page_text,
    snapshot_chunks,
    source_storage_root,
)

router = APIRouter(prefix="/api/admin/project-context", tags=["admin-project-context"])

SUMMARY_SYSTEM_PROMPT = """你是项目背景资料整理助手。只能依据给定资料生成摘要，不得补充外部事实。
摘要必须使用中文 Markdown，并固定包含：项目定位、目标、关键术语、整体架构、关键约束、已确认边界、不可推断项、来源清单。
优先保留项目事实，避免复述和展开无关的通用知识；不设置固定字数上限。
遇到资料冲突时明确列出冲突，不得自行选择。不要输出 API Key、密钥、请求头或疑似凭据。"""

OUTLINE_SYSTEM_PROMPT = """你是课程大纲整理助手。只能依据当前视频已选择的课件文本生成中文 Markdown 大纲，不得补充外部事实。
大纲应完整覆盖本视频对应页区间的主题、关键概念、步骤或案例约束，并列出资料未说明的边界。
不设置固定字数上限，但避免机械复述、无关扩写和疑似密钥信息。"""


class DraftUpdate(BaseModel):
    version_id: int | None = None
    summary_text: str = Field(min_length=1)


class PublishRequest(BaseModel):
    version_id: int


class VideoKnowledgeUpdate(BaseModel):
    source_id: int
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    course_type: Literal["theory", "practice"] = "theory"


class VideoCourseTypeUpdate(BaseModel):
    course_type: Literal["theory", "practice"]


class VideoOutlineUpdate(BaseModel):
    outline_text: str = ""


def _serialize_source(source: ProjectSource) -> dict:
    return {
        "id": source.id,
        "filename": source.original_filename,
        "format": source.source_format,
        "sha256": source.source_hash,
        "status": source.status,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "page_count": len(ppt_pages(source)),
    }


def _serialize_video_knowledge(
    material: Material,
    knowledge: VideoKnowledge | None,
    source: ProjectSource | None,
) -> dict:
    return {
        "course_id": material.course_id,
        "video_name": material.video_original_filename or material.course_id,
        "course_type": knowledge.course_type if knowledge else "theory",
        "source_id": knowledge.source_id if knowledge else None,
        "source_filename": source.original_filename if source else None,
        "page_start": knowledge.page_start if knowledge else None,
        "page_end": knowledge.page_end if knowledge else None,
        "knowledge_text": (knowledge.knowledge_text_cached or "") if knowledge else "",
        "knowledge_filename": (
            Path(knowledge.knowledge_text_path).name
            if knowledge and knowledge.knowledge_text_path
            else None
        ),
        "outline_text": (knowledge.outline_text_cached or "") if knowledge else "",
        "outline_status": knowledge.outline_status if knowledge else "empty",
        "subtitle_included": knowledge.subtitle_included if knowledge else False,
        "legacy_context": knowledge is None,
    }


def _get_material(db: Session, course_id: str) -> Material:
    material = db.query(Material).filter(Material.course_id == course_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="视频课程不存在")
    return material


def _knowledge_file(material: Material, filename: str, content: str) -> str:
    root = storage._materials_root().resolve()
    directory = (root / material.course_id / "_knowledge").resolve()
    if not directory.is_relative_to(root):
        raise HTTPException(status_code=400, detail="课程目录无效")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    temp_path = path.with_suffix(path.suffix + ".part")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)
    return str(path)


def _serialize_version(version: ProjectContextVersion | None) -> dict | None:
    if version is None:
        return None
    return {
        "id": version.id,
        "version": version.version,
        "summary_text": version.summary_text,
        "status": version.status,
        "is_stale": version.is_stale,
        "source_manifest": json.loads(version.source_manifest_json or "[]"),
        "updated_at": version.updated_at.isoformat() if version.updated_at else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


@router.get("")
def get_project_context(
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    bindings = (
        db.query(ProjectMaterial)
        .filter(ProjectMaterial.project_id == project.id)
        .order_by(ProjectMaterial.id.asc())
        .all()
    )
    materials = [db.get(Material, binding.material_id) for binding in bindings]
    materials = [material for material in materials if material is not None]
    knowledge_by_material = {
        item.material_id: item
        for item in db.query(VideoKnowledge).filter(
            VideoKnowledge.material_id.in_([material.id for material in materials])
        ).all()
    } if materials else {}
    sources_by_id = {source.id: source for source in active_sources(db, project.id)}
    db.commit()
    return {
        "project": {"project_key": project.project_key, "name": project.name},
        "sources": [_serialize_source(source) for source in active_sources(db, project.id)],
        "published": _serialize_version(latest_published_version(db, project.id)),
        "draft": _serialize_version(latest_draft_version(db, project.id)),
        "material_count": len(materials),
        "videos": [
            _serialize_video_knowledge(
                material,
                knowledge_by_material.get(material.id),
                sources_by_id.get(knowledge_by_material[material.id].source_id)
                if material.id in knowledge_by_material
                else None,
            )
            for material in materials
        ],
    }


@router.post("/sources")
async def upload_project_source(
    file: UploadFile,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    original_filename = file.filename or ""
    filename_error = storage.validate_filename(original_filename)
    if filename_error:
        raise HTTPException(status_code=400, detail=filename_error)
    extension = storage.validate_extension("courseware", original_filename)
    if extension is None or not extension.startswith("."):
        raise HTTPException(status_code=400, detail=extension or "不支持的资料格式")

    root = source_storage_root() / project.project_key
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"source_{uuid.uuid4().hex}{extension}"
    temp_path = destination.with_suffix(destination.suffix + ".part")
    max_bytes = storage.FILE_TYPES["courseware"]["max_bytes"]
    head = b""
    total = 0
    digest = hashlib.sha256()
    try:
        with temp_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                if not head:
                    head = chunk[:16]
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=400, detail="项目资料文件过大，上限 50MB")
                output.write(chunk)
                digest.update(chunk)
        magic_error = storage.validate_magic("courseware", extension, head)
        if magic_error:
            raise HTTPException(status_code=400, detail=magic_error)
        temp_path.replace(destination)
        extracted_text, _, warning = extract_courseware(destination, extension.lstrip("."))
        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail=warning or "未能从项目资料中提取文本")
        source = ProjectSource(
            project_id=project.id,
            original_filename=original_filename,
            source_format=extension.lstrip("."),
            file_path=str(destination),
            text_cached=extracted_text,
            source_hash=digest.hexdigest(),
            status="active",
        )
        db.add(source)
        db.flush()
        mark_published_stale(db, project.id)
        db.commit()
        db.refresh(source)
        return {
            "source": _serialize_source(source),
            "warning": warning,
            "summary_refresh_required": True,
        }
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    except Exception:
        temp_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise


@router.delete("/sources/{source_id}")
def delete_project_source(
    source_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    source = db.query(ProjectSource).filter(
        ProjectSource.id == source_id,
        ProjectSource.project_id == project.id,
        ProjectSource.status == "active",
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="项目资料不存在")
    referenced = db.query(VideoKnowledge).filter(VideoKnowledge.source_id == source.id).count()
    if referenced:
        raise HTTPException(
            status_code=409,
            detail=f"该课件已被 {referenced} 个视频引用，请先重新绑定这些视频",
        )
    path = Path(source.file_path).resolve()
    root = source_storage_root()
    if path.is_relative_to(root):
        path.unlink(missing_ok=True)
    source.status = "deleted"
    db.add(source)
    mark_published_stale(db, project.id)
    db.commit()
    return {"message": "项目资料已删除", "summary_refresh_required": True}


@router.get("/sources/{source_id}/pages")
def get_source_pages(
    source_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    source = db.query(ProjectSource).filter(
        ProjectSource.id == source_id,
        ProjectSource.project_id == project.id,
        ProjectSource.status == "active",
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="课件不存在")
    if source.source_format != "pptx":
        raise HTTPException(status_code=400, detail="视频页区间当前仅支持 PPTX 课件")
    return {"source": _serialize_source(source), "pages": ppt_pages(source)}


@router.put("/videos/{course_id}/course-type")
def update_video_course_type(
    course_id: str,
    body: VideoCourseTypeUpdate,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    material = _get_material(db, course_id)
    knowledge = ensure_video_knowledge(db, material, body.course_type)
    db.commit()
    db.refresh(knowledge)
    source = db.get(ProjectSource, knowledge.source_id) if knowledge.source_id else None
    return {"video": _serialize_video_knowledge(material, knowledge, source)}


@router.put("/videos/{course_id}/knowledge")
def build_video_knowledge(
    course_id: str,
    body: VideoKnowledgeUpdate,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    material = _get_material(db, course_id)
    project = ensure_default_project(db)
    source = db.query(ProjectSource).filter(
        ProjectSource.id == body.source_id,
        ProjectSource.project_id == project.id,
        ProjectSource.status == "active",
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="课件不存在")
    pages = ppt_pages(source)
    if not pages:
        raise HTTPException(status_code=400, detail="该课件没有可选择的 PPT 文本页")
    first_page, last_page = pages[0]["page"], pages[-1]["page"]
    if body.page_start > body.page_end:
        raise HTTPException(status_code=400, detail="起始页不能大于结束页")
    if body.page_start < first_page or body.page_end > last_page:
        raise HTTPException(
            status_code=400,
            detail=f"页码范围应在 {first_page}–{last_page} 之间",
        )
    selected = select_ppt_page_text(source, body.page_start, body.page_end)
    if not selected:
        raise HTTPException(status_code=400, detail="所选页区间没有可提取文本")
    rendered = (
        f"# {material.video_original_filename or material.course_id} · 课程知识\n\n"
        f"来源：{source.original_filename} 第 {body.page_start}–{body.page_end} 页\n\n"
        f"{selected}"
    )
    knowledge = ensure_video_knowledge(db, material, body.course_type)
    knowledge.source_id = source.id
    knowledge.page_start = body.page_start
    knowledge.page_end = body.page_end
    knowledge.knowledge_text_cached = rendered
    knowledge.knowledge_text_path = _knowledge_file(material, "course-knowledge.md", rendered)
    # 页区间改变后旧大纲已失去依据，必须由管理员重新生成或编辑。
    knowledge.outline_text_cached = None
    knowledge.outline_text_path = None
    knowledge.outline_status = "empty"
    knowledge.subtitle_included = False
    db.add(knowledge)
    db.commit()
    db.refresh(knowledge)
    return {"video": _serialize_video_knowledge(material, knowledge, source)}


@router.post("/videos/{course_id}/outline/generate")
async def generate_video_outline(
    course_id: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    material = _get_material(db, course_id)
    knowledge = ensure_video_knowledge(db, material)
    if not (knowledge.knowledge_text_cached or "").strip():
        raise HTTPException(status_code=400, detail="请先选择 PPT 页区间并生成课程知识文本")
    if knowledge.course_type != "practice":
        raise HTTPException(status_code=400, detail="理论/通用课程无需大纲，请先切换为实战/案例")
    config = get_default_config(db)
    if config is None:
        raise HTTPException(status_code=400, detail="未配置大模型，请先完成模型配置")
    api_key = decrypt_api_key(config.api_key_encrypted)
    if not api_key:
        raise HTTPException(status_code=400, detail="大模型 API Key 无效")
    outcome = RoutingOutcome()
    async for _ in stream_model_chain(
        db,
        config,
        api_key,
        [
            {"role": "system", "content": OUTLINE_SYSTEM_PROMPT},
            {"role": "user", "content": knowledge.knowledge_text_cached or ""},
        ],
        outcome,
        stream_chat,
        # 管理员生成任务先按真实模型耗时完整等待，后续再依据实测数据决定超时策略。
        deadline_seconds=None,
    ):
        pass
    if not outcome.success or not outcome.answer.strip():
        knowledge.outline_status = "error"
        db.add(knowledge)
        db.commit()
        raise HTTPException(status_code=502, detail="视频课程大纲生成失败，请检查模型状态后重试")
    outline = outcome.answer.strip()
    knowledge.outline_text_cached = outline
    knowledge.outline_text_path = _knowledge_file(material, "course-outline.md", outline)
    knowledge.outline_status = "draft"
    db.add(knowledge)
    db.commit()
    db.refresh(knowledge)
    source = db.get(ProjectSource, knowledge.source_id) if knowledge.source_id else None
    return {"video": _serialize_video_knowledge(material, knowledge, source)}


@router.put("/videos/{course_id}/outline")
def update_video_outline(
    course_id: str,
    body: VideoOutlineUpdate,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    material = _get_material(db, course_id)
    knowledge = ensure_video_knowledge(db, material)
    if knowledge.course_type != "practice":
        raise HTTPException(status_code=400, detail="理论/通用课程不启用视频大纲")
    if not (knowledge.knowledge_text_cached or "").strip():
        raise HTTPException(status_code=400, detail="请先选择 PPT 页区间并生成课程知识文本")
    outline = body.outline_text.strip()
    knowledge.outline_text_cached = outline or None
    knowledge.outline_text_path = _knowledge_file(material, "course-outline.md", outline)
    knowledge.outline_status = "ready" if outline else "empty"
    db.add(knowledge)
    db.commit()
    db.refresh(knowledge)
    source = db.get(ProjectSource, knowledge.source_id) if knowledge.source_id else None
    return {"video": _serialize_video_knowledge(material, knowledge, source)}


@router.post("/summary/generate")
async def generate_summary_draft(
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    sources = active_sources(db, project.id)
    if not sources:
        raise HTTPException(status_code=400, detail="请先上传项目资料")
    combined = "\n\n".join(
        f"【资料：{source.original_filename}】\n{source.text_cached}" for source in sources
    )
    if estimate_tokens(combined) > SUMMARY_SOURCE_TOKEN_LIMIT:
        raise HTTPException(status_code=400, detail="项目资料超过摘要生成上限，请拆分或精简后重试")

    config = get_default_config(db)
    if config is None:
        raise HTTPException(status_code=400, detail="未配置大模型，请先完成模型配置")
    api_key = decrypt_api_key(config.api_key_encrypted)
    if not api_key:
        raise HTTPException(status_code=400, detail="大模型 API Key 无效")
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": combined},
    ]
    outcome = RoutingOutcome()
    async for _ in stream_model_chain(
        db,
        config,
        api_key,
        messages,
        outcome,
        stream_chat,
        # 管理员生成任务先按真实模型耗时完整等待，后续再依据实测数据决定超时策略。
        deadline_seconds=None,
    ):
        pass
    if not outcome.success or not outcome.answer.strip():
        raise HTTPException(status_code=502, detail="项目背景摘要生成失败，请检查模型状态后重试")
    draft = latest_draft_version(db, project.id)
    if draft is None:
        draft = ProjectContextVersion(
            project_id=project.id,
            version=next_version_number(db, project.id),
            created_by=current.id,
        )
    draft.summary_text = outcome.answer.strip()
    draft.source_manifest_json = manifest_json(sources)
    draft.is_stale = False
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return {"draft": _serialize_version(draft)}


@router.put("/summary/draft")
def update_summary_draft(
    body: DraftUpdate,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    sources = active_sources(db, project.id)
    if not sources:
        raise HTTPException(status_code=400, detail="请先上传项目资料")
    if body.version_id is None:
        draft = latest_draft_version(db, project.id)
        if draft is None:
            draft = ProjectContextVersion(
                project_id=project.id,
                version=next_version_number(db, project.id),
                source_manifest_json=manifest_json(sources),
                created_by=current.id,
            )
        else:
            draft.source_manifest_json = manifest_json(sources)
    else:
        draft = db.query(ProjectContextVersion).filter(
            ProjectContextVersion.id == body.version_id,
            ProjectContextVersion.project_id == project.id,
            ProjectContextVersion.status == "draft",
        ).first()
        if draft is None:
            raise HTTPException(status_code=404, detail="摘要草稿不存在")
    summary = body.summary_text.strip()
    draft.summary_text = summary
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return {"draft": _serialize_version(draft)}


@router.post("/summary/publish")
def publish_summary(
    body: PublishRequest,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = ensure_default_project(db)
    draft = db.query(ProjectContextVersion).filter(
        ProjectContextVersion.id == body.version_id,
        ProjectContextVersion.project_id == project.id,
        ProjectContextVersion.status == "draft",
    ).first()
    if draft is None:
        raise HTTPException(status_code=404, detail="摘要草稿不存在")
    if not draft.summary_text.strip():
        raise HTTPException(status_code=400, detail="摘要草稿不能为空")
    sources = active_sources(db, project.id)
    if draft.source_manifest_json != manifest_json(sources):
        raise HTTPException(status_code=409, detail="项目资料已变化，请重新生成摘要草稿")

    previous = latest_published_version(db, project.id)
    if previous is not None:
        previous.status = "superseded"
        db.add(previous)
    draft.status = "published"
    draft.is_stale = False
    draft.published_by = current.id
    draft.published_at = datetime.now(timezone.utc)
    db.add(draft)
    db.flush()
    snapshot_chunks(db, draft, sources)
    db.commit()
    db.refresh(draft)
    return {"published": _serialize_version(draft)}
