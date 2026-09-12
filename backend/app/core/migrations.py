"""SQLite 轻量迁移（本项目没有 Alembic）。

为什么需要这个文件：
    `Base.metadata.create_all()` 只会创建**不存在的表**，
    对已经存在的表**不会补列**。所以每新增一个字段，都必须在这里配一条幂等 ALTER TABLE，
    否则老库会一直缺列，ORM 查询时抛 `no such column`。

约定：
    - 每条迁移写成「检测 → 执行」，可重复运行（应用每次启动都会跑一遍）
    - 表不存在时直接跳过（create_all 会用当前模型建表，新列已包含在内）
    - 删除废弃列仅使用 SQLite 3.35+ 原生 DROP COLUMN；旧版本记录警告并跳过
"""

import logging
import unicodedata
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _table_exists(engine: Engine, table: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).fetchone()
    return row is not None


def _table_columns(engine: Engine, table: str) -> set[str]:
    """返回表已有的列名集合（SQLite: PRAGMA table_info）。"""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _add_column_if_missing(engine: Engine, table: str, column: str, ddl: str) -> bool:
    """列不存在才加。返回是否真的执行了。"""
    if not _table_exists(engine, table):
        # 表还没建：create_all 会带新列一起建，无需 ALTER
        return False
    if column in _table_columns(engine, table):
        return False
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    logger.info("migration applied: %s.%s", table, column)
    return True


def _sqlite_supports_drop_column(engine: Engine) -> bool:
    with engine.connect() as conn:
        version = str(conn.execute(text("SELECT sqlite_version()")).scalar_one())
    try:
        return tuple(int(part) for part in version.split(".")[:3]) >= (3, 35, 0)
    except ValueError:
        logger.warning("无法识别 SQLite 版本 %s，跳过 DROP COLUMN", version)
        return False


def _drop_column_if_exists(engine: Engine, table: str, column: str) -> bool:
    """SQLite 3.35+ 下幂等删列；旧 SQLite 安全跳过。"""
    if not _table_exists(engine, table) or column not in _table_columns(engine, table):
        return False
    if not _sqlite_supports_drop_column(engine):
        logger.warning("SQLite 版本低于 3.35，跳过删除 %s.%s", table, column)
        return False
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    logger.info("migration applied: drop %s.%s", table, column)
    return True


def _column_is_not_null(engine: Engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column and bool(row[3]) for row in rows)


def _normalise_series_name(filename: str) -> tuple[str, str]:
    name = unicodedata.normalize("NFC", Path(filename).stem.strip())
    return name, name.casefold()


def _ensure_series_context_v1(engine: Engine) -> bool:
    """Create and backfill the independent series identity for legacy PPT columns."""
    if not _table_exists(engine, "project_sources"):
        return False

    project_source_columns = _table_columns(engine, "project_sources")
    video_columns = _table_columns(engine, "video_knowledge") if _table_exists(engine, "video_knowledge") else set()
    log_columns = _table_columns(engine, "llm_call_logs") if _table_exists(engine, "llm_call_logs") else set()
    session_columns = _table_columns(engine, "column_chat_sessions") if _table_exists(engine, "column_chat_sessions") else set()

    # The schema transition, not file metadata, identifies legacy data. The ALTERs,
    # backfill and session rebuild below share one transaction so a retry cannot see
    # a newly added series_id column with incomplete historical data.
    backfill_legacy_sources = "series_id" not in project_source_columns
    rebuild_sessions = bool(session_columns) and (
        "series_id" not in session_columns
        or "memory_context_epoch" not in session_columns
        or _column_is_not_null(engine, "column_chat_sessions", "source_id")
    )
    changed = False
    with engine.begin() as conn:
        # sqlite3 legacy transaction mode does not BEGIN for DDL automatically.
        # An explicit transaction keeps schema changes and their backfill atomic.
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS content_series (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id),
                name VARCHAR(128) NOT NULL,
                normalized_name VARCHAR(128) NOT NULL,
                context_epoch INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_content_series_project_name UNIQUE (project_id, normalized_name)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_content_series_project_id ON content_series(project_id)"
        ))

        for table, columns, ddl in (
            ("project_sources", project_source_columns, "INTEGER REFERENCES content_series(id)"),
            ("video_knowledge", video_columns, "INTEGER REFERENCES content_series(id)"),
            ("llm_call_logs", log_columns, "INTEGER REFERENCES content_series(id) ON DELETE SET NULL"),
        ):
            if columns and "series_id" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN series_id {ddl}"))
                changed = True

        if backfill_legacy_sources:
            sources = conn.execute(text("""
                SELECT id, project_id, original_filename
                FROM project_sources
                WHERE status = 'active' AND lower(source_format) = 'pptx' AND series_id IS NULL
                ORDER BY id
            """)).fetchall()
            for source_id, project_id, filename in sources:
                name, normalized_name = _normalise_series_name(filename)
                existing = conn.execute(text("""
                    SELECT id FROM content_series
                    WHERE project_id = :project_id AND normalized_name = :normalized_name
                """), {"project_id": project_id, "normalized_name": normalized_name}).fetchone()
                if existing is not None:
                    raise RuntimeError(f"active PPT source name conflicts with series {existing[0]}: {filename}")
                conn.execute(text("""
                    INSERT INTO content_series
                        (id, project_id, name, normalized_name, context_epoch, created_at, updated_at)
                    VALUES (:id, :project_id, :name, :normalized_name, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """), {
                    "id": source_id,
                    "project_id": project_id,
                    "name": name,
                    "normalized_name": normalized_name,
                })
                conn.execute(
                    text("UPDATE project_sources SET series_id = :series_id WHERE id = :source_id"),
                    {"series_id": source_id, "source_id": source_id},
                )
                changed = True

            if video_columns:
                result = conn.execute(text("""
                    UPDATE video_knowledge
                    SET series_id = (SELECT series_id FROM project_sources WHERE id = video_knowledge.source_id)
                    WHERE series_id IS NULL AND source_id IS NOT NULL
                """))
                changed = changed or bool(result.rowcount)
            if log_columns:
                result = conn.execute(text("""
                    UPDATE llm_call_logs
                    SET series_id = (SELECT series_id FROM project_sources WHERE id = llm_call_logs.source_id)
                    WHERE series_id IS NULL AND source_id IS NOT NULL
                """))
                changed = changed or bool(result.rowcount)

        if rebuild_sessions:
            missing = conn.execute(text("""
                SELECT COUNT(*) FROM column_chat_sessions c
                LEFT JOIN project_sources s ON s.id = c.source_id
                WHERE s.series_id IS NULL
            """)).scalar_one()
            if missing:
                raise RuntimeError(f"{missing} column chat session(s) have no series mapping")
            conn.execute(text("""
                CREATE TABLE column_chat_sessions_v1 (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    series_id INTEGER NOT NULL REFERENCES content_series(id),
                    source_id INTEGER REFERENCES project_sources(id),
                    session_id VARCHAR(64) NOT NULL UNIQUE REFERENCES chat_sessions(session_id),
                    memory_summary TEXT NOT NULL DEFAULT '',
                    memory_context_epoch INTEGER NOT NULL DEFAULT 1,
                    summarized_through_message_id INTEGER,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_column_chat_user_series UNIQUE (user_id, series_id)
                )
            """))
            conn.execute(text("""
                INSERT INTO column_chat_sessions_v1
                    (id, user_id, series_id, source_id, session_id, memory_summary,
                     memory_context_epoch, summarized_through_message_id, created_at, updated_at)
                SELECT c.id, c.user_id, s.series_id, c.source_id, c.session_id,
                       c.memory_summary, 1, c.summarized_through_message_id,
                       c.created_at, c.updated_at
                FROM column_chat_sessions c
                JOIN project_sources s ON s.id = c.source_id
            """))
            conn.execute(text("DROP TABLE column_chat_sessions"))
            conn.execute(text("ALTER TABLE column_chat_sessions_v1 RENAME TO column_chat_sessions"))
            conn.execute(text(
                "CREATE INDEX ix_column_chat_sessions_user_id ON column_chat_sessions(user_id)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_column_chat_sessions_series_id ON column_chat_sessions(series_id)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_column_chat_sessions_source_id ON column_chat_sessions(source_id)"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX ix_column_chat_sessions_session_id ON column_chat_sessions(session_id)"
            ))
            changed = True
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_project_sources_current_series
            ON project_sources(series_id) WHERE series_id IS NOT NULL
        """))
        if video_columns:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_video_knowledge_series_id ON video_knowledge(series_id)"
            ))
        if log_columns:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_llm_call_logs_series_id ON llm_call_logs(series_id)"
            ))
    return changed


def run_migrations(engine: Engine) -> list[str]:
    """执行全部幂等迁移，返回本次实际执行的迁移名（便于启动日志观察）。"""
    applied: list[str] = []

    # A3（2026-09-04）：字幕人工校对状态（历史列名继续兼容）。
    # subtitle_status 表示生成生命周期；review_state 仅表示是否经过人工校对。
    # AI Evidence 准入由 ready + 字幕文件存在决定，与 review_state 无关。
    if _add_column_if_missing(
        engine,
        "materials",
        "review_state",
        "VARCHAR(16) NOT NULL DEFAULT 'unreviewed'",
    ):
        applied.append("materials.review_state")

    # v9 S2：取消不是持久业务状态；旧列仅用于过渡，现在幂等清理。
    if _drop_column_if_exists(engine, "materials", "subtitle_canceled"):
        applied.append("materials.subtitle_canceled")

    if _ensure_series_context_v1(engine):
        applied.append("series_context_v1")

    return applied
