"""项目级背景、版本化摘要和轻量证据检索。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, settings
from app.models.models import (
    ChatContextBinding,
    Material,
    Project,
    ProjectChunk,
    ProjectContextVersion,
    ProjectMaterial,
    ProjectSource,
    VideoKnowledge,
)

DEFAULT_PROJECT_KEY = "default-study-project"
DEFAULT_PROJECT_NAME = "默认学习项目"
PROJECT_EVIDENCE_TOKEN_BUDGET = 12_000
SUMMARY_SOURCE_TOKEN_LIMIT = 20_000
MAX_RETRIEVED_CHUNKS = 8
_CHUNK_MAX_CHARS = 1_600
_CHUNK_OVERLAP = 160


def estimate_tokens(value: str) -> int:
    """沿用项目中文约 1 字 1 token 的保守估算。"""
    return len(value)


def source_storage_root() -> Path:
    configured = Path(settings.project_context_dir)
    root = configured if configured.is_absolute() else PROJECT_ROOT / configured
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def ensure_default_project(db: Session) -> Project:
    project = db.query(Project).filter(Project.project_key == DEFAULT_PROJECT_KEY).first()
    if project is None:
        project = Project(project_key=DEFAULT_PROJECT_KEY, name=DEFAULT_PROJECT_NAME)
        db.add(project)
        db.flush()
    return project


def ensure_video_knowledge(
    db: Session,
    material: Material,
    course_type: str | None = None,
) -> VideoKnowledge:
    knowledge = db.query(VideoKnowledge).filter(VideoKnowledge.material_id == material.id).first()
    if knowledge is None:
        knowledge = VideoKnowledge(
            material_id=material.id,
            course_type=course_type or "theory",
        )
        db.add(knowledge)
        db.flush()
    elif course_type is not None:
        knowledge.course_type = course_type
        db.add(knowledge)
    return knowledge


def bind_material(
    db: Session,
    material: Material,
    project: Project | None = None,
    *,
    course_type: str | None = None,
) -> ProjectMaterial:
    project = project or ensure_default_project(db)
    binding = db.query(ProjectMaterial).filter(ProjectMaterial.material_id == material.id).first()
    if binding is None:
        binding = ProjectMaterial(project_id=project.id, material_id=material.id)
        db.add(binding)
        db.flush()
    # 旧素材没有视频级分类时继续使用既有项目摘要；只有显式选择课程类型才切到新流程。
    if course_type is not None:
        ensure_video_knowledge(db, material, course_type)
    return binding


def bind_all_materials(db: Session) -> Project:
    project = ensure_default_project(db)
    for material in db.query(Material).order_by(Material.id.asc()).all():
        bind_material(db, material, project)
    db.commit()
    return project


def get_project_for_material(db: Session, material: Material | None) -> Project | None:
    if material is None:
        return None
    binding = db.query(ProjectMaterial).filter(ProjectMaterial.material_id == material.id).first()
    if binding is None:
        bind_material(db, material)
        db.commit()
        binding = db.query(ProjectMaterial).filter(ProjectMaterial.material_id == material.id).first()
    return db.get(Project, binding.project_id) if binding else None


def active_sources(db: Session, project_id: int) -> list[ProjectSource]:
    return (
        db.query(ProjectSource)
        .filter(ProjectSource.project_id == project_id, ProjectSource.status == "active")
        .order_by(ProjectSource.id.asc())
        .all()
    )


_PPT_PAGE_HEADING = re.compile(r"(?m)^【第(\d+)页(?:\s+([^】]*))?】\s*$")


def ppt_pages(source: ProjectSource) -> list[dict]:
    """从已缓存的 PPT 文本恢复逐页内容，避免再次读取原文件。"""
    if source.source_format != "pptx":
        return []
    matches = list(_PPT_PAGE_HEADING.finditer(source.text_cached or ""))
    pages: list[dict] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source.text_cached)
        body = source.text_cached[match.end() : end].strip()
        title = (match.group(2) or "").strip()
        label = f"第{int(match.group(1))}页" + (f" {title}" if title else "")
        pages.append(
            {
                "page": int(match.group(1)),
                "title": title,
                "text": f"【{label}】\n{body}".strip(),
            }
        )
    return pages


def select_ppt_page_text(source: ProjectSource, page_start: int, page_end: int) -> str:
    pages = ppt_pages(source)
    selected = [page["text"] for page in pages if page_start <= page["page"] <= page_end]
    return "\n\n".join(selected).strip()


def source_manifest(sources: list[ProjectSource]) -> list[dict]:
    return [
        {
            "source_id": source.id,
            "filename": source.original_filename,
            "format": source.source_format,
            "sha256": source.source_hash,
        }
        for source in sources
    ]


def manifest_json(sources: list[ProjectSource]) -> str:
    return json.dumps(source_manifest(sources), ensure_ascii=False, sort_keys=True)


def latest_published_version(db: Session, project_id: int) -> ProjectContextVersion | None:
    return (
        db.query(ProjectContextVersion)
        .filter(
            ProjectContextVersion.project_id == project_id,
            ProjectContextVersion.status == "published",
        )
        .order_by(ProjectContextVersion.version.desc())
        .first()
    )


def latest_draft_version(db: Session, project_id: int) -> ProjectContextVersion | None:
    return (
        db.query(ProjectContextVersion)
        .filter(
            ProjectContextVersion.project_id == project_id,
            ProjectContextVersion.status == "draft",
        )
        .order_by(ProjectContextVersion.version.desc())
        .first()
    )


def next_version_number(db: Session, project_id: int) -> int:
    latest = db.query(func.max(ProjectContextVersion.version)).filter(
        ProjectContextVersion.project_id == project_id
    ).scalar()
    return int(latest or 0) + 1


def mark_published_stale(db: Session, project_id: int) -> bool:
    version = latest_published_version(db, project_id)
    if version is None:
        return False
    version.is_stale = True
    db.add(version)
    return True


def get_or_create_session_binding(
    db: Session,
    session_id: str,
    project: Project | None,
) -> ChatContextBinding | None:
    existing = db.query(ChatContextBinding).filter(ChatContextBinding.session_id == session_id).first()
    if existing is not None:
        return existing
    if project is None:
        return None
    version = latest_published_version(db, project.id)
    if version is None:
        return None
    binding = ChatContextBinding(
        session_id=session_id,
        project_id=project.id,
        context_version_id=version.id,
    )
    db.add(binding)
    db.flush()
    return binding


def split_source_into_chunks(source: ProjectSource) -> list[tuple[str, str]]:
    text_value = source.text_cached.strip()
    if not text_value:
        return []

    sections: list[tuple[str, str]] = []
    if source.source_format == "pptx":
        parts = re.split(r"(?=【第\d+页(?:\s[^】]*)?】)", text_value)
        for index, part in enumerate(parts, start=1):
            part = part.strip()
            if not part:
                continue
            match = re.match(r"【([^】]+)】", part)
            sections.append((match.group(1) if match else f"第{index}页", part))
    elif source.source_format == "md":
        current_label = "正文"
        current: list[str] = []
        for line in text_value.splitlines():
            if line.lstrip().startswith("#"):
                if current:
                    sections.append((current_label, "\n".join(current).strip()))
                current_label = line.lstrip("# ").strip() or "未命名章节"
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append((current_label, "\n".join(current).strip()))
    else:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text_value) if part.strip()]
        sections = [(f"片段{index}", part) for index, part in enumerate(paragraphs, start=1)]

    chunks: list[tuple[str, str]] = []
    for label, section in sections or [("正文", text_value)]:
        if len(section) <= _CHUNK_MAX_CHARS:
            chunks.append((label, section))
            continue
        start = 0
        part_index = 1
        while start < len(section):
            end = min(len(section), start + _CHUNK_MAX_CHARS)
            chunks.append((f"{label} · {part_index}", section[start:end]))
            if end >= len(section):
                break
            start = max(start + 1, end - _CHUNK_OVERLAP)
            part_index += 1
    return chunks


def ensure_fts_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS project_chunks_fts USING fts5(
                chunk_id UNINDEXED,
                project_id UNINDEXED,
                context_version_id UNINDEXED,
                content,
                tokenize='trigram'
            )
            """
        )
    )


def snapshot_chunks(db: Session, version: ProjectContextVersion, sources: list[ProjectSource]) -> None:
    ensure_fts_table(db)
    db.query(ProjectChunk).filter(ProjectChunk.context_version_id == version.id).delete(
        synchronize_session=False
    )
    db.execute(
        text("DELETE FROM project_chunks_fts WHERE context_version_id = :version_id"),
        {"version_id": version.id},
    )
    ordinal = 0
    created: list[ProjectChunk] = []
    for source in sources:
        for section_label, content in split_source_into_chunks(source):
            ordinal += 1
            chunk = ProjectChunk(
                project_id=version.project_id,
                context_version_id=version.id,
                source_id=source.id,
                source_filename=source.original_filename,
                section_label=section_label,
                ordinal=ordinal,
                content=content,
            )
            db.add(chunk)
            created.append(chunk)
    db.flush()
    for chunk in created:
        db.execute(
            text(
                """
                INSERT INTO project_chunks_fts(
                    chunk_id, project_id, context_version_id, content
                ) VALUES (:chunk_id, :project_id, :version_id, :content)
                """
            ),
            {
                "chunk_id": chunk.id,
                "project_id": chunk.project_id,
                "version_id": chunk.context_version_id,
                "content": chunk.content,
            },
        )


def _search_terms(question: str) -> list[str]:
    candidates: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_@.#+-]{3,}|[\u4e00-\u9fff]{3,}", question):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            candidates.extend(token[index : index + 3] for index in range(len(token) - 2))
        else:
            candidates.append(token)
    unique = list(dict.fromkeys(candidates))
    if len(unique) > 12:
        # 对整句均匀采样并保留尾部，避免长中文问题只检索开头、漏掉句尾专名。
        last = len(unique) - 1
        indexes = [round(index * last / 11) for index in range(12)]
        unique = [unique[index] for index in dict.fromkeys(indexes)]
    # FTS5 查询里的标点需要转义；三引号短语可避免操作符注入。
    return [term.replace('"', '""') for term in unique if len(term) >= 3]


def select_evidence_chunks(
    db: Session,
    version: ProjectContextVersion,
    question: str,
) -> tuple[list[ProjectChunk], str]:
    chunks = (
        db.query(ProjectChunk)
        .filter(ProjectChunk.context_version_id == version.id)
        .order_by(ProjectChunk.ordinal.asc())
        .all()
    )
    if _evidence_tokens(chunks) <= PROJECT_EVIDENCE_TOKEN_BUDGET:
        return chunks, "full"

    ensure_fts_table(db)
    terms = _search_terms(question)
    ids: list[int] = []
    if terms:
        query = " OR ".join(f'"{term}"' for term in terms)
        rows = db.execute(
            text(
                """
                SELECT CAST(chunk_id AS INTEGER) AS chunk_id
                FROM project_chunks_fts
                WHERE project_chunks_fts MATCH :query
                  AND project_id = :project_id
                  AND context_version_id = :version_id
                ORDER BY bm25(project_chunks_fts)
                LIMIT :limit
                """
            ),
            {
                "query": query,
                "project_id": version.project_id,
                "version_id": version.id,
                "limit": MAX_RETRIEVED_CHUNKS,
            },
        ).all()
        ids = [int(row.chunk_id) for row in rows]
    if not ids:
        return _fit_evidence_budget(chunks[:MAX_RETRIEVED_CHUNKS]), "fallback"
    by_id = {chunk.id: chunk for chunk in chunks}
    ranked = [by_id[chunk_id] for chunk_id in ids if chunk_id in by_id]
    return _fit_evidence_budget(ranked), "fts5"


def _render_evidence_chunk(chunk: ProjectChunk, content: str | None = None) -> str:
    return (
        f"【来源：{chunk.source_filename} · {chunk.section_label}】\n"
        f"{chunk.content if content is None else content}"
    )


def _evidence_tokens(chunks: list[ProjectChunk]) -> int:
    return estimate_tokens("\n\n".join(_render_evidence_chunk(chunk) for chunk in chunks))


def _fit_evidence_budget(chunks: list[ProjectChunk]) -> list[ProjectChunk]:
    """按最终注入文本的实际长度硬限制证据预算，保持原排序。"""
    selected: list[ProjectChunk] = []
    for chunk in chunks:
        candidate = [*selected, chunk]
        if _evidence_tokens(candidate) <= PROJECT_EVIDENCE_TOKEN_BUDGET:
            selected.append(chunk)
    # 正常分块远小于预算；保底避免未来分块参数变化导致完全无证据。
    return selected or chunks[:1]


def build_evidence_text(chunks: list[ProjectChunk]) -> str:
    parts: list[str] = []
    used = 0
    for chunk in chunks:
        separator = "\n\n" if parts else ""
        header = f"【来源：{chunk.source_filename} · {chunk.section_label}】\n"
        remaining = PROJECT_EVIDENCE_TOKEN_BUDGET - used - estimate_tokens(separator + header)
        if remaining <= 0:
            break
        content = chunk.content[:remaining]
        part = f"{separator}{header}{content}"
        parts.append(part)
        used += estimate_tokens(part)
        if len(content) < len(chunk.content):
            break
    return "".join(parts)


def version_context(
    db: Session,
    binding: ChatContextBinding | None,
    question: str,
) -> tuple[str, str, dict]:
    if binding is None:
        return "", "", {"project_context": "unavailable"}
    version = db.get(ProjectContextVersion, binding.context_version_id)
    project = db.get(Project, binding.project_id)
    # superseded 版本仍服务于已绑定的旧会话；只有新会话才选择最新 published。
    if version is None or project is None or version.status not in {"published", "superseded"}:
        return "", "", {"project_context": "unavailable"}
    chunks, retrieval_mode = select_evidence_chunks(db, version, question)
    return (
        version.summary_text,
        build_evidence_text(chunks),
        {
            "project_key": project.project_key,
            "project_context_version": version.version,
            "project_context_stale": version.is_stale,
            "retrieval_mode": retrieval_mode,
            "source_ids": sorted({chunk.source_id for chunk in chunks}),
            "chunk_ids": [chunk.id for chunk in chunks],
        },
    )
