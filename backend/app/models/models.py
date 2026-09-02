"""数据模型（对应 PRD 第 9 章，5 张表）。"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # 'admin' | 'user'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ModelRoute(Base):
    """同一模型凭据下可按优先级调度的具体模型。"""

    __tablename__ = "model_routes"
    __table_args__ = (
        UniqueConstraint("model_config_id", "model_name", name="uq_model_route_config_model"),
        Index("ix_model_routes_schedule", "model_config_id", "is_enabled", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    model_config_id: Mapped[int] = mapped_column(ForeignKey("model_configs.id"), index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health_status: Mapped[str] = mapped_column(String(32), default="healthy", nullable=False)
    failure_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    course_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    dir_path: Mapped[str] = mapped_column(String(512), nullable=False)
    video_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subtitle_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    subtitle_source_format: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 'vtt'|'srt'
    subtitle_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending/generating/ready/error
    subtitle_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 'manual'|'whisper'
    subtitle_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    courseware_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    courseware_format: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 'md'|'pdf'|'pptx'
    courseware_text_cached: Mapped[str | None] = mapped_column(Text, nullable=True)
    courseware_has_chapters: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    video_original_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    subtitle_original_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    courseware_original_filename: Mapped[str | None] = mapped_column(String(256), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ready", nullable=False)  # 'ready'|'error'
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Project(Base):
    """可由多个视频共享的项目级知识空间。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ProjectMaterial(Base):
    """项目与课程视频的关联；P0 默认所有视频属于唯一项目。"""

    __tablename__ = "project_materials"
    __table_args__ = (
        UniqueConstraint("project_id", "material_id", name="uq_project_material"),
        Index("ix_project_material_material", "material_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProjectSource(Base):
    """管理员明确上传的项目共享资料。"""

    __tablename__ = "project_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    source_format: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    text_cached: Mapped[str] = mapped_column(Text, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class VideoKnowledge(Base):
    """单个视频从共享 PPT 页区间提取的知识文本与可选大纲。"""

    __tablename__ = "video_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id"), unique=True, index=True, nullable=False
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_sources.id"), index=True, nullable=True
    )
    course_type: Mapped[str] = mapped_column(
        String(16), default="theory", nullable=False
    )  # theory | practice
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    knowledge_text_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    knowledge_text_cached: Mapped[str | None] = mapped_column(Text, nullable=True)
    outline_text_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    outline_text_cached: Mapped[str | None] = mapped_column(Text, nullable=True)
    outline_status: Mapped[str] = mapped_column(String(16), default="empty", nullable=False)
    subtitle_included: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ProjectContextVersion(Base):
    """项目背景摘要及其不可变来源快照。"""

    __tablename__ = "project_context_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_project_context_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_manifest_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProjectChunk(Base):
    """随 Summary 版本冻结的原始证据片段。"""

    __tablename__ = "project_chunks"
    __table_args__ = (
        Index("ix_project_chunks_version_ordinal", "context_version_id", "ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True, nullable=False)
    context_version_id: Mapped[int] = mapped_column(
        ForeignKey("project_context_versions.id"), index=True, nullable=False
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("project_sources.id"), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    section_label: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class ChatContextBinding(Base):
    """固定一个会话所使用的项目背景版本，避免多轮中途漂移。"""

    __tablename__ = "chat_context_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    context_version_id: Mapped[int] = mapped_column(ForeignKey("project_context_versions.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    course_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    selected_subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_subtitle_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_subtitle_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    messages_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    model_config_id: Mapped[int | None] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
