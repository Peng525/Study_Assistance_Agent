"""数据模型（对应 PRD 第 9 章，5 张表）。"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
