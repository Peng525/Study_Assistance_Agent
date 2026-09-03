"""Admin LLM call log API and retention tests."""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_llm_call_logs import router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import LLMCallLog, User
from app.services.llm_audit import create_call_log, update_call_log


def _headers(user_id: int, username: str, role: str):
    return {"Authorization": f"Bearer {create_access_token(user_id, username, role)}"}


def _record(db_session, index: int, *, user_id: int = 2, status: str = "success"):
    messages = [
        {"role": "system", "content": "系统规则"},
        {"role": "user", "content": f"问题{index}"},
    ]
    return create_call_log(
        db_session.get_bind(),
        request_id=f"request-{index}",
        user_id=user_id,
        username_snapshot=f"user{user_id}",
        session_id="session-1",
        course_id="spring-ioc",
        video_name="005.Spring - IoC和DI.mp4",
        source_id=7,
        start_time=125,
        user_question=f"问题{index}",
        request_messages_json=json.dumps(messages, ensure_ascii=False),
        prompt_chars=sum(len(item["content"]) for item in messages),
        status=status,
        attempted_models_json='["qwen-plus"]',
        final_model_name="qwen-plus" if status == "success" else None,
        answer_text="回答" if status == "success" else "",
        answer_chars=2 if status == "success" else 0,
    )


def _client(db_session):
    db_session.add_all(
        [
            User(username="admin", password_hash=hash_password("123456"), role="admin"),
            User(username="user25", password_hash=hash_password("123456"), role="user"),
        ]
    )
    db_session.commit()

    def override_db():
        yield db_session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_admin_can_filter_view_detail_and_clear_logs(db_session):
    client = _client(db_session)
    first_id = _record(db_session, 1, user_id=2)
    _record(db_session, 2, user_id=3, status="failed")

    response = client.get(
        "/api/admin/llm-call-logs?user_id=2&status=success&page=1&page_size=10",
        headers=_headers(1, "admin", "admin"),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["user_id"] == 2

    detail = client.get(
        f"/api/admin/llm-call-logs/{first_id}",
        headers=_headers(1, "admin", "admin"),
    ).json()
    assert detail["video_name"] == "005.Spring - IoC和DI.mp4"
    assert detail["request_messages"][1]["content"] == "问题1"
    assert detail["answer_text"] == "回答"
    assert "api_key" not in json.dumps(detail).lower()
    assert "authorization" not in json.dumps(detail).lower()

    forbidden = client.get(
        "/api/admin/llm-call-logs", headers=_headers(2, "user25", "user")
    )
    assert forbidden.status_code == 403

    cleared = client.delete(
        "/api/admin/llm-call-logs", headers=_headers(1, "admin", "admin")
    )
    assert cleared.json()["deleted_count"] == 2
    assert db_session.query(LLMCallLog).count() == 0


def test_retention_keeps_latest_500_logs(db_session):
    for index in range(501):
        assert _record(db_session, index) is not None
    db_session.expire_all()
    records = db_session.query(LLMCallLog).order_by(LLMCallLog.id.asc()).all()
    assert len(records) == 500
    assert records[0].request_id == "request-1"
    assert records[-1].request_id == "request-500"


def test_audit_storage_failure_does_not_raise_or_log_sensitive_values(monkeypatch, caplog):
    secret_prompt = "SECRET_PROJECT_PROMPT"
    secret_answer = "SECRET_MODEL_ANSWER"

    class FailingSession:
        def __init__(self, **_kwargs):
            pass

        def add(self, _record):
            raise RuntimeError(f"database failure with params {secret_prompt} {secret_answer}")

        def get(self, _model, _record_id):
            return SimpleNamespace()

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("app.services.llm_audit.Session", FailingSession)

    log_id = create_call_log(
        object(),
        request_id="safe-request-id",
        user_id=1,
        username_snapshot="user",
        user_question=secret_prompt,
        request_messages_json=json.dumps([{"role": "user", "content": secret_prompt}]),
        answer_text=secret_answer,
    )
    update_call_log(object(), 99, answer_text=secret_answer)

    assert log_id is None
    assert "safe-request-id" in caplog.text
    assert "log_id=99" in caplog.text
    assert secret_prompt not in caplog.text
    assert secret_answer not in caplog.text
