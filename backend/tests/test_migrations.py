"""SQLite 幂等迁移测试（review_state 加列 + 废弃列清理）。

项目没有 Alembic，新增字段全靠 `app/core/migrations.py` 的 ALTER TABLE。
这个文件锁住它的三个关键行为：能加列 / 重复运行不报错 / 老数据有默认值。
"""

from sqlalchemy import create_engine, text

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
