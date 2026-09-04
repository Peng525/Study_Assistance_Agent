"""SQLite 幂等迁移测试（A3：materials.review_state 加列）。

项目没有 Alembic，新增字段全靠 `app/core/migrations.py` 的 ALTER TABLE。
这个文件锁住它的三个关键行为：能加列 / 重复运行不报错 / 老数据有默认值。
"""

from sqlalchemy import create_engine, text

from app.core.migrations import _table_columns, run_migrations

_OLD_MATERIALS = """
CREATE TABLE materials (
    id INTEGER PRIMARY KEY,
    course_id VARCHAR(128),
    subtitle_status VARCHAR(16)
)
"""


def _make_old_db():
    """模拟升级前的老库：materials 表没有 review_state。"""
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

    assert applied == ["materials.review_state"]
    assert "review_state" in _table_columns(engine, "materials")


def test_existing_rows_get_default_value():
    """ALTER TABLE 必须给已有行填上 unreviewed，不能留 NULL 让后续判断炸掉。"""
    engine = _make_old_db()
    run_migrations(engine)

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, review_state FROM materials ORDER BY id")).fetchall()

    assert [r[1] for r in rows] == ["unreviewed", "unreviewed"]


def test_is_idempotent():
    """每次启动都会跑一遍，重复执行必须无副作用。"""
    engine = _make_old_db()
    assert run_migrations(engine) == ["materials.review_state"]
    assert run_migrations(engine) == []
    assert run_migrations(engine) == []


def test_missing_table_is_skipped():
    """表还没建时跳过（create_all 会带新列一起建），不能抛异常。"""
    engine = create_engine("sqlite:///:memory:")
    assert run_migrations(engine) == []


def test_no_op_when_column_already_present():
    """全新库经 create_all 建表后，列已存在，迁移应识别为空操作。"""
    from app.core.database import Base
    from app.models import models  # noqa: F401 — 注册模型到 Base.metadata

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    assert "review_state" in _table_columns(engine, "materials")
    assert run_migrations(engine) == []
