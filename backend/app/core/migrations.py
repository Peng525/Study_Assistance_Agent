"""SQLite 轻量迁移（本项目没有 Alembic）。

为什么需要这个文件：
    `Base.metadata.create_all()` 只会创建**不存在的表**，
    对已经存在的表**不会补列**。所以每新增一个字段，都必须在这里配一条幂等 ALTER TABLE，
    否则老库会一直缺列，ORM 查询时抛 `no such column`。

约定：
    - 每条迁移写成「检测 → 执行」，可重复运行（应用每次启动都会跑一遍）
    - 表不存在时直接跳过（create_all 会用当前模型建表，新列已包含在内）
    - 只做加列 / 建索引这类安全的增量操作；改列类型、删列请手工处理
"""

import logging

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


def run_migrations(engine: Engine) -> list[str]:
    """执行全部幂等迁移，返回本次实际执行的迁移名（便于启动日志观察）。"""
    applied: list[str] = []

    # A3（2026-09-04）：字幕审核状态。
    # 与 subtitle_status 分工：subtitle_status = 字幕**有没有生成好**（pending/generating/ready/error）
    #                        review_state   = 字幕**能不能作为自动 AI 证据**（unreviewed/reviewed）
    # 两者正交：生成完成（ready）不等于审核通过（reviewed）。
    if _add_column_if_missing(
        engine,
        "materials",
        "review_state",
        "VARCHAR(16) NOT NULL DEFAULT 'unreviewed'",
    ):
        applied.append("materials.review_state")

    return applied
