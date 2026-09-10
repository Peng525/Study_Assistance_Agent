"""SQLite 幂等迁移测试（review_state 加列 + 废弃列清理）。

项目没有 Alembic，新增字段全靠 `app/core/migrations.py` 的 ALTER TABLE。
这个文件锁住它的三个关键行为：能加列 / 重复运行不报错 / 老数据有默认值。
"""

import pytest

from sqlalchemy import create_engine, event, text

from app.core import migrations
from app.core.migrations import _table_columns, run_migrations

_OLD_MATERIALS = """
CREATE TABLE materials (
    id INTEGER PRIMARY KEY,
    course_id VARCHAR(128),
    subtitle_status VARCHAR(16)
)
"""

_LEGACY_CANCELED_MATERIALS = """
CREATE TABLE materials (
    id INTEGER PRIMARY KEY,
    course_id VARCHAR(128),
    subtitle_status VARCHAR(16),
    subtitle_canceled BOOLEAN NOT NULL DEFAULT 0
)
"""


# 每次新增迁移都要往这里追加。用常量而不是散落在断言里的字面量，
# 避免"加了一个迁移、两个测试各改一次"造成的漂移。
EXPECTED_MIGRATIONS = ["materials.review_state"]


_LEGACY_SERIES_SCHEMA = """
CREATE TABLE projects (id INTEGER PRIMARY KEY, project_key VARCHAR(128), name VARCHAR(128));
CREATE TABLE users (id INTEGER PRIMARY KEY);
CREATE TABLE chat_sessions (session_id VARCHAR(64) PRIMARY KEY);
CREATE TABLE project_sources (
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, original_filename VARCHAR(256) NOT NULL,
    source_format VARCHAR(16) NOT NULL, file_path VARCHAR(512) NOT NULL,
    text_cached TEXT NOT NULL, source_hash VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE video_knowledge (
    id INTEGER PRIMARY KEY, material_id INTEGER NOT NULL UNIQUE, source_id INTEGER
);
CREATE TABLE llm_call_logs (id INTEGER PRIMARY KEY, source_id INTEGER);
CREATE TABLE column_chat_sessions (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, source_id INTEGER NOT NULL,
    session_id VARCHAR(64) NOT NULL UNIQUE, memory_summary TEXT NOT NULL DEFAULT '',
    summarized_through_message_id INTEGER, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
    CONSTRAINT uq_column_chat_user_source UNIQUE (user_id, source_id)
);
"""


def _make_old_db():
    """模拟最初的老库：materials 表还没有 review_state。"""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(_OLD_MATERIALS))
        conn.execute(
            text("INSERT INTO materials (id, course_id, subtitle_status) VALUES (1,'c1','pending')")
        )
        conn.execute(
            text("INSERT INTO materials (id, course_id, subtitle_status) VALUES (2,'c2','ready')")
        )
    return engine


def test_adds_column_to_old_table():
    engine = _make_old_db()
    assert "review_state" not in _table_columns(engine, "materials")

    applied = run_migrations(engine)

    assert applied == EXPECTED_MIGRATIONS
    assert "review_state" in _table_columns(engine, "materials")
    assert "subtitle_canceled" not in _table_columns(engine, "materials")


def test_existing_rows_get_default_value():
    """ALTER TABLE 必须给已有行填上 unreviewed，不能留 NULL 让后续判断炸掉。"""
    engine = _make_old_db()
    run_migrations(engine)

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, review_state FROM materials ORDER BY id")).fetchall()

    assert [r[1] for r in rows] == ["unreviewed", "unreviewed"]


def test_drops_legacy_canceled_column_without_losing_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(_LEGACY_CANCELED_MATERIALS))
        conn.execute(text("INSERT INTO materials VALUES (1, 'c1', 'pending', 0)"))
        conn.execute(text("INSERT INTO materials VALUES (2, 'c2', 'ready', 1)"))

    assert run_migrations(engine) == ["materials.review_state", "materials.subtitle_canceled"]
    assert "subtitle_canceled" not in _table_columns(engine, "materials")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, course_id, subtitle_status, review_state FROM materials ORDER BY id")
        ).fetchall()
    assert rows == [
        (1, "c1", "pending", "unreviewed"),
        (2, "c2", "ready", "unreviewed"),
    ]


def test_old_sqlite_skips_drop_column(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(_LEGACY_CANCELED_MATERIALS))
    monkeypatch.setattr(migrations, "_sqlite_supports_drop_column", lambda _engine: False)

    assert run_migrations(engine) == ["materials.review_state"]
    assert "subtitle_canceled" in _table_columns(engine, "materials")


def test_is_idempotent():
    """每次启动都会跑一遍，重复执行必须无副作用。"""
    engine = _make_old_db()
    assert run_migrations(engine) == EXPECTED_MIGRATIONS
    assert run_migrations(engine) == []
    assert run_migrations(engine) == []


def test_missing_table_is_skipped():
    """表还没建时跳过（create_all 会带新列一起建），不能抛异常。"""
    engine = create_engine("sqlite:///:memory:")
    assert run_migrations(engine) == []


def test_no_op_for_current_schema():
    """全新库经 create_all 建表后，迁移应识别为空操作。"""
    from app.core.database import Base
    from app.models import models  # noqa: F401 — 注册模型到 Base.metadata

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    assert "review_state" in _table_columns(engine, "materials")
    assert "subtitle_canceled" not in _table_columns(engine, "materials")
    assert run_migrations(engine) == []


def test_series_migration_backfills_ppt_videos_logs_and_session():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for statement in _LEGACY_SERIES_SCHEMA.split(";"):
            if statement.strip():
                conn.execute(text(statement))
        conn.execute(text("INSERT INTO projects VALUES (1, 'default', '默认项目')"))
        conn.execute(text("INSERT INTO users VALUES (2)"))
        conn.execute(text("INSERT INTO chat_sessions VALUES ('keep-session')"))
        conn.execute(text("""
            INSERT INTO project_sources VALUES
            (2, 1, 'Spring.pptx', 'pptx', 'spring.pptx', 'text', 'hash', 'active',
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            (3, 1, 'old.pptx', 'pptx', 'old.pptx', 'old', 'oldhash', 'deleted',
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
        conn.execute(text("INSERT INTO video_knowledge VALUES (1, 10, 2), (2, 11, NULL)"))
        conn.execute(text("INSERT INTO llm_call_logs VALUES (1, 2)"))
        conn.execute(text("""
            INSERT INTO column_chat_sessions VALUES
            (1, 2, 2, 'keep-session', 'memory', 99, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))

    assert run_migrations(engine) == ["series_context_v1"]
    with engine.connect() as conn:
        series = conn.execute(text(
            "SELECT id, name, normalized_name, context_epoch FROM content_series"
        )).fetchall()
        videos = conn.execute(text(
            "SELECT material_id, series_id FROM video_knowledge ORDER BY material_id"
        )).fetchall()
        session = conn.execute(text("""
            SELECT session_id, series_id, source_id, memory_summary, memory_context_epoch,
                   summarized_through_message_id
            FROM column_chat_sessions
        """)).one()
        log_series = conn.execute(text("SELECT series_id FROM llm_call_logs")).scalar_one()
    assert series == [(2, "Spring", "spring", 1)]
    assert videos == [(10, 2), (11, None)]
    assert session == ("keep-session", 2, 2, "memory", 1, 99)
    assert log_series == 2

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO project_sources
                (id, project_id, original_filename, source_format, file_path, text_cached,
                 source_hash, status, created_at, updated_at, series_id)
            VALUES
                (4, 1, 'project-background.pptx', 'pptx', 'background.pptx', 'project text',
                 'project-hash', 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
        """))

    assert run_migrations(engine) == []
    assert run_migrations(engine) == []
    with engine.connect() as conn:
        project_source = conn.execute(text(
            "SELECT series_id FROM project_sources WHERE id = 4"
        )).scalar_one()
        series_count = conn.execute(text("SELECT COUNT(*) FROM content_series")).scalar_one()
    assert project_source is None
    assert series_count == 1


def test_series_migration_rolls_back_schema_and_backfill_on_failure():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY)"))
        conn.execute(text("""
            CREATE TABLE project_sources (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
                original_filename VARCHAR(256) NOT NULL, source_format VARCHAR(16) NOT NULL,
                status VARCHAR(16) NOT NULL
            )
        """))
        conn.execute(text("INSERT INTO projects VALUES (1)"))
        conn.execute(text("""
            INSERT INTO project_sources VALUES
            (1, 1, 'Spring.pptx', 'pptx', 'active'),
            (2, 1, 'SPRING.PPTX', 'pptx', 'active')
        """))

    with pytest.raises(RuntimeError, match="conflicts with series"):
        run_migrations(engine)

    assert "series_id" not in _table_columns(engine, "project_sources")
    with engine.connect() as conn:
        content_series_exists = conn.execute(text("""
            SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'content_series'
        """)).fetchone()
        sources = conn.execute(text(
            "SELECT id, original_filename FROM project_sources ORDER BY id"
        )).fetchall()
    assert content_series_exists is None
    assert sources == [(1, "Spring.pptx"), (2, "SPRING.PPTX")]


def test_series_migration_rolls_back_late_session_rebuild_failure():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for statement in _LEGACY_SERIES_SCHEMA.split(";"):
            if statement.strip():
                conn.execute(text(statement))
        conn.execute(text("INSERT INTO projects VALUES (1, 'default', '默认项目')"))
        conn.execute(text("INSERT INTO users VALUES (2)"))
        conn.execute(text("INSERT INTO chat_sessions VALUES ('keep-session')"))
        conn.execute(text("""
            INSERT INTO project_sources VALUES
            (2, 1, 'Spring.pptx', 'pptx', 'spring.pptx', 'text', 'hash', 'active',
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
        conn.execute(text("INSERT INTO video_knowledge VALUES (1, 10, 2)"))
        conn.execute(text("INSERT INTO llm_call_logs VALUES (1, 2)"))
        conn.execute(text("""
            INSERT INTO column_chat_sessions VALUES
            (1, 2, 2, 'keep-session', 'memory', 99, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))

    def fail_after_session_swap(_conn, _cursor, statement, _parameters, _context, _many):
        if "CREATE INDEX ix_column_chat_sessions_series_id" in statement:
            raise RuntimeError("forced session rebuild failure")

    event.listen(engine, "before_cursor_execute", fail_after_session_swap)
    try:
        with pytest.raises(RuntimeError, match="forced session rebuild failure"):
            run_migrations(engine)
    finally:
        event.remove(engine, "before_cursor_execute", fail_after_session_swap)

    assert "series_id" not in _table_columns(engine, "project_sources")
    assert "series_id" not in _table_columns(engine, "video_knowledge")
    assert "series_id" not in _table_columns(engine, "llm_call_logs")
    assert "series_id" not in _table_columns(engine, "column_chat_sessions")
    assert _column_is_not_null_for_test(engine, "column_chat_sessions", "source_id")
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'content_series'"
        )).fetchone() is None
        assert conn.execute(text(
            "SELECT source_id, session_id, memory_summary, summarized_through_message_id "
            "FROM column_chat_sessions"
        )).one() == (2, "keep-session", "memory", 99)


def _column_is_not_null_for_test(engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column and bool(row[3]) for row in rows)
