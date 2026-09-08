"""v8 启动孤儿 `generating` 复位测试（`seed._reconcile_orphan_subtitle_tasks`）。

背景：B1 修好 root-cause 之后，DB 才可能残留 `generating` —— 在那之前手动触发
永远写 `pending`，自然不会有残留。修好之后，进程一重启内存队列就是空的，
残留的 `generating` 行永远不会被任何 worker 推进，前端会一直显示"生成中"并空轮询。

复位策略：改回 `pending`，**不自动重新入队**（启动即满载转写会拖慢首个请求、
吃掉 CPU/内存），由管理员在素材管理页用批量操作显式触发。
"""

from app.core.seed import _reconcile_orphan_subtitle_tasks
from app.models.models import Material


def _add(db, course_id, *, subtitle_status, review_state="unreviewed", **kw):
    material = Material(
        course_id=course_id,
        dir_path=f"/m/{course_id}",
        status="ready",
        subtitle_status=subtitle_status,
        review_state=review_state,
        **kw,
    )
    db.add(material)
    db.commit()
    return material


def test_orphan_generating_reset_to_pending(db_session):
    _add(db_session, "c1", subtitle_status="generating", subtitle_source="whisper")
    _add(db_session, "c2", subtitle_status="generating")
    _add(db_session, "c3", subtitle_status="ready", review_state="reviewed")
    _add(db_session, "c4", subtitle_status="error")
    _add(db_session, "c5", subtitle_status="pending")

    _reconcile_orphan_subtitle_tasks(db_session)

    c1 = db_session.query(Material).filter_by(course_id="c1").one()
    c2 = db_session.query(Material).filter_by(course_id="c2").one()
    assert c1.subtitle_status == "pending"
    assert c2.subtitle_status == "pending"
    # 有视频的行保留 whisper 来源标记，方便管理员一眼看出"这条本来在自动生成"
    assert c1.subtitle_source == "whisper"

    # 非 generating 的行必须原样不动
    c3 = db_session.query(Material).filter_by(course_id="c3").one()
    assert c3.subtitle_status == "ready"
    assert c3.review_state == "reviewed", "复位不能顺手冲掉已审核结论"
    assert db_session.query(Material).filter_by(course_id="c4").one().subtitle_status == "error"
    assert db_session.query(Material).filter_by(course_id="c5").one().subtitle_status == "pending"


def test_no_orphan_is_noop(db_session):
    """没有孤儿时不该有任何写入。"""
    _add(db_session, "c1", subtitle_status="ready", review_state="reviewed")

    _reconcile_orphan_subtitle_tasks(db_session)

    c1 = db_session.query(Material).filter_by(course_id="c1").one()
    assert c1.subtitle_status == "ready"
    assert c1.review_state == "reviewed"


def test_empty_table_is_safe(db_session):
    """空表不炸（首次启动时 materials 表可能还没有任何行）。"""
    assert db_session.query(Material).count() == 0
    _reconcile_orphan_subtitle_tasks(db_session)
    assert db_session.query(Material).count() == 0
