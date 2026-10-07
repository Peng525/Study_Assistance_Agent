import pytest
from sqlalchemy import create_engine, text
from app.core.migrations import run_migrations
from app.services.material_title import resolve_display_title


@pytest.mark.parametrize("title,filename,expected", [
    (" 自定义标题 ", "004.old.mp4", "自定义标题"),
    (None, "004.Spring - 容器和组件.mp4", "004.Spring - 容器和组件"),
    (" ", "课程.v2.MOV", "课程.v2"),
    (None, "长标题.mkv", "长标题"),
    (None, None, "当前视频"),
])
def test_title_priority(title, filename, expected):
    assert resolve_display_title(title, filename) == expected


def test_add_title_column_without_backfilling_history():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE materials (id INTEGER PRIMARY KEY, video_original_filename TEXT, review_state TEXT)"))
        conn.execute(text("INSERT INTO materials VALUES (1, '004.Spring.mp4', 'reviewed')"))
    assert run_migrations(engine) == ["materials.display_title"]
    with engine.begin() as conn:
        assert conn.execute(text("SELECT * FROM materials")).one() == (1, "004.Spring.mp4", "reviewed", None)
        conn.execute(text("UPDATE materials SET display_title='用户标题' WHERE id=1"))
    assert run_migrations(engine) == []
    assert run_migrations(engine) == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT display_title FROM materials")).scalar_one() == "用户标题"
