import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.admin_columns import router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import (
    ChatSession,
    ColumnChatSession,
    ContentSeries,
    LLMCallLog,
    ProjectSource,
    User,
)
from app.services.project_context import ensure_default_project


@pytest.fixture()
def client(db_session):
    db_session.add(User(username="admin", password_hash=hash_password("123456"), role="admin"))
    db_session.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: (yield db_session)
    return TestClient(app)


def _headers():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def test_series_crud_normalizes_name_and_keeps_audit_logs(client, db_session):
    created = client.post("/api/admin/columns", json={"name": "  Ｓpring  "}, headers=_headers())
    assert created.status_code == 200
    series_id = created.json()["id"]
    duplicate = client.post("/api/admin/columns", json={"name": "ＳPRING"}, headers=_headers())
    assert duplicate.status_code == 409
    renamed = client.put(
        f"/api/admin/columns/{series_id}", json={"name": "Spring 基础"}, headers=_headers()
    )
    assert renamed.status_code == 200

    log = LLMCallLog(
        request_id="audit-1", user_id=2, username_snapshot="user25",
        user_question="q", series_id=series_id,
    )
    db_session.add(log)
    db_session.commit()
    deleted = client.delete(f"/api/admin/columns/{series_id}", headers=_headers())
    assert deleted.status_code == 200
    db_session.refresh(log)
    assert log.series_id is None


def test_series_delete_rejects_ppt_and_real_session(client, db_session):
    series = client.post("/api/admin/columns", json={"name": "Spring"}, headers=_headers()).json()
    project = ensure_default_project(db_session)
    source = ProjectSource(
        project_id=project.id, series_id=series["id"], original_filename="Spring.pptx",
        source_format="pptx", file_path="x", text_cached="x", source_hash="a" * 64,
        status="active",
    )
    db_session.add(source)
    db_session.commit()
    assert client.delete(f"/api/admin/columns/{series['id']}", headers=_headers()).status_code == 409
    source.series_id = None
    source.status = "deleted"
    session = ChatSession(session_id="real", user_id=1, messages_json=json.dumps([{"role": "user", "content": "q"}]))
    binding = ColumnChatSession(user_id=1, series_id=series["id"], session_id="real")
    db_session.add_all([session, binding])
    db_session.commit()
    assert client.delete(f"/api/admin/columns/{series['id']}", headers=_headers()).status_code == 409


def test_database_prevents_two_current_ppts(db_session):
    project = ensure_default_project(db_session)
    series = ContentSeries(project_id=project.id, name="Spring", normalized_name="spring")
    db_session.add(series)
    db_session.flush()
    for index in (1, 2):
        db_session.add(ProjectSource(
            project_id=project.id, series_id=series.id, original_filename=f"{index}.pptx",
            source_format="pptx", file_path=str(index), text_cached="x",
            source_hash=str(index) * 64, status="active",
        ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
