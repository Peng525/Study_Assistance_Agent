"""模块 3.1 模型配置 CRUD 测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin_model_configs import router as mc_router
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.models.models import ModelConfig, ModelRoute, SystemSetting, User
from app.services.llm_client import LLMError
from app.services.llm_errors import classify_provider_error
from app.services.model_router import DASHSCOPE_CURRENT_PRESET, routing_mode_key


@pytest.fixture()
def client(db_session):
    admin = User(username="admin", password_hash=hash_password("123456"), role="admin")
    user = User(username="user25", password_hash=hash_password("123456"), role="user")
    db_session.add_all([admin, user])
    db_session.commit()

    def _get_db_override():
        yield db_session

    app = FastAPI()
    app.include_router(mc_router)
    app.dependency_overrides[get_db] = _get_db_override
    return TestClient(app)


def _h():
    return {"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"}


def test_create_and_list(client):
    resp = client.post(
        "/api/admin/model-configs",
        json={"name": "qwen", "base_url": "https://x", "api_key": "sk-test1234", "model_name": "qwen-plus", "is_default": True},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json()["api_key_masked"] == "sk-****1234"

    lst = client.get("/api/admin/model-configs", headers=_h()).json()
    assert len(lst) == 1
    assert lst[0]["is_default"] is True


def test_create_empty_key_rejected(client):
    resp = client.post(
        "/api/admin/model-configs",
        json={"name": "x", "base_url": "https://x", "api_key": "", "model_name": "m", "is_default": False},
        headers=_h(),
    )
    assert resp.status_code == 400


def test_default_unique(client):
    for i in range(2):
        client.post(
            "/api/admin/model-configs",
            json={"name": f"c{i}", "base_url": "https://x", "api_key": f"sk-key{i}", "model_name": "m", "is_default": True},
            headers=_h(),
        )
    lst = client.get("/api/admin/model-configs", headers=_h()).json()
    defaults = [c for c in lst if c["is_default"]]
    assert len(defaults) == 1


def test_update_keep_key_when_empty(client):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "qwen", "base_url": "https://x", "api_key": "sk-orig9999", "model_name": "qwen-plus", "is_default": False},
        headers=_h(),
    ).json()["id"]
    # 编辑时 api_key 留空 → 不修改
    resp = client.put(
        f"/api/admin/model-configs/{cid}",
        json={"name": "qwen2", "base_url": "https://y", "api_key": "", "model_name": "qwen-max", "is_default": True},
        headers=_h(),
    )
    assert resp.status_code == 200
    assert resp.json()["api_key_masked"] == "sk-****9999"  # 原 key 保留
    assert resp.json()["model_name"] == "qwen-max"


def test_first_config_becomes_default_and_default_cannot_be_unset(client):
    created = client.post(
        "/api/admin/model-configs",
        json={"name": "first", "base_url": "https://x", "api_key": "key", "model_name": "m", "is_default": False},
        headers=_h(),
    ).json()
    assert created["is_default"] is True

    response = client.put(
        f"/api/admin/model-configs/{created['id']}",
        json={"name": "first", "base_url": "https://x", "api_key": "", "model_name": "m", "is_default": False},
        headers=_h(),
    )
    assert response.status_code == 400
    assert "必须保留一个默认 API" in response.json()["detail"]


def test_update_not_found(client):
    resp = client.put(
        "/api/admin/model-configs/999",
        json={"name": "x", "base_url": "https://x", "api_key": "", "model_name": "m", "is_default": False},
        headers=_h(),
    )
    assert resp.status_code == 404


def test_delete(client):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "qwen", "base_url": "https://x", "api_key": "sk-test1234", "model_name": "qwen-plus", "is_default": False},
        headers=_h(),
    ).json()["id"]
    assert client.delete(f"/api/admin/model-configs/{cid}", headers=_h()).status_code == 200
    assert client.get("/api/admin/model-configs", headers=_h()).json() == []


def test_list_keeps_creation_order_and_delete_default_promotes_oldest(client):
    ids = []
    for index in range(3):
        ids.append(
            client.post(
                "/api/admin/model-configs",
                json={
                    "name": f"config-{index}",
                    "base_url": "https://x",
                    "api_key": f"key-{index}",
                    "model_name": "m",
                    "is_default": index == 0,
                },
                headers=_h(),
            ).json()["id"]
        )

    assert [item["id"] for item in client.get("/api/admin/model-configs", headers=_h()).json()] == ids

    assert client.delete(f"/api/admin/model-configs/{ids[0]}", headers=_h()).status_code == 200
    remaining = client.get("/api/admin/model-configs", headers=_h()).json()
    assert [item["id"] for item in remaining] == ids[1:]
    assert remaining[0]["is_default"] is True
    assert remaining[1]["is_default"] is False


def test_delete_non_default_keeps_current_default(client):
    default_id = client.post(
        "/api/admin/model-configs",
        json={"name": "default", "base_url": "https://x", "api_key": "key-1", "model_name": "m", "is_default": True},
        headers=_h(),
    ).json()["id"]
    other_id = client.post(
        "/api/admin/model-configs",
        json={"name": "other", "base_url": "https://x", "api_key": "key-2", "model_name": "m", "is_default": False},
        headers=_h(),
    ).json()["id"]

    assert client.delete(f"/api/admin/model-configs/{other_id}", headers=_h()).status_code == 200
    remaining = client.get("/api/admin/model-configs", headers=_h()).json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == default_id
    assert remaining[0]["is_default"] is True


def test_requires_admin(client):
    token = create_access_token(2, "user25", "user")
    resp = client.get("/api/admin/model-configs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_dashscope_preset_route_crud_order_and_idempotency(client, db_session):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "chain", "base_url": "https://x", "api_key": "key", "model_name": "qwen-plus", "is_default": True},
        headers=_h(),
    ).json()["id"]
    preset_url = f"/api/admin/model-configs/{cid}/routes/presets/{DASHSCOPE_CURRENT_PRESET}"
    first = client.post(preset_url, headers=_h())
    second = client.post(preset_url, headers=_h())
    assert first.status_code == 200
    assert [item["model_name"] for item in first.json()] == [item["model_name"] for item in second.json()]
    assert len(first.json()) == 10
    assert first.json()[0]["model_name"] == "qwen3.8-max"
    assert first.json()[-1]["model_name"] == "qwen-plus"
    assert db_session.query(ModelConfig).filter(ModelConfig.id == cid).one().api_key_encrypted != "key"

    route = first.json()[0]
    updated = client.put(
        f"/api/admin/model-configs/{cid}/routes/{route['id']}",
        json={**{key: route[key] for key in ("display_name", "model_name", "priority")}, "priority": 999, "is_enabled": False},
        headers=_h(),
    )
    assert updated.status_code == 200
    assert updated.json()["is_enabled"] is False
    listed = client.get(f"/api/admin/model-configs/{cid}/routes", headers=_h()).json()
    assert listed[-1]["id"] == route["id"]

    assert client.post(f"/api/admin/model-configs/{cid}/routes/{route['id']}/reset", headers=_h()).status_code == 200
    assert client.delete(f"/api/admin/model-configs/{cid}/routes/{route['id']}", headers=_h()).status_code == 200
    assert db_session.query(ModelRoute).filter(ModelRoute.id == route["id"]).first() is None


def test_unknown_route_preset_is_rejected(client):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "custom", "base_url": "https://x", "api_key": "key", "model_name": "m", "is_default": True},
        headers=_h(),
    ).json()["id"]

    response = client.post(
        f"/api/admin/model-configs/{cid}/routes/presets/not-a-provider",
        headers=_h(),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "模型模板不存在"


def test_model_routes_are_isolated_per_api_config(client):
    config_ids = []
    for name in ("dashscope", "deepseek"):
        config_ids.append(
            client.post(
                "/api/admin/model-configs",
                json={
                    "name": name,
                    "base_url": f"https://{name}.example/v1",
                    "api_key": f"key-{name}",
                    "model_name": "shared-model",
                    "is_default": name == "dashscope",
                },
                headers=_h(),
            ).json()["id"]
        )

    for config_id in config_ids:
        first = client.post(
            f"/api/admin/model-configs/{config_id}/routes",
            json={"display_name": "Shared", "model_name": "shared-model", "priority": 10, "is_enabled": True},
            headers=_h(),
        )
        duplicate = client.post(
            f"/api/admin/model-configs/{config_id}/routes",
            json={"display_name": "Duplicate", "model_name": "shared-model", "priority": 20, "is_enabled": True},
            headers=_h(),
        )
        assert first.status_code == 200
        assert duplicate.status_code == 409

    first_routes = client.get(f"/api/admin/model-configs/{config_ids[0]}/routes", headers=_h()).json()
    second_routes = client.get(f"/api/admin/model-configs/{config_ids[1]}/routes", headers=_h()).json()
    assert len(first_routes) == len(second_routes) == 1
    assert first_routes[0]["model_config_id"] != second_routes[0]["model_config_id"]


def test_delete_config_deletes_routes(client, db_session):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "chain", "base_url": "https://x", "api_key": "key", "model_name": "m", "is_default": False},
        headers=_h(),
    ).json()["id"]
    client.post(
        f"/api/admin/model-configs/{cid}/routes",
        json={"display_name": "m1", "model_name": "m1", "priority": 10, "is_enabled": True},
        headers=_h(),
    )
    assert db_session.get(SystemSetting, routing_mode_key(cid)) is not None
    assert client.delete(f"/api/admin/model-configs/{cid}", headers=_h()).status_code == 200
    assert db_session.query(ModelRoute).filter(ModelRoute.model_config_id == cid).count() == 0
    assert db_session.get(SystemSetting, routing_mode_key(cid)) is None


def test_batch_connectivity_uses_real_classifier_and_updates_state(client, db_session, monkeypatch):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "chain", "base_url": "https://x", "api_key": "key", "model_name": "m", "is_default": True},
        headers=_h(),
    ).json()["id"]
    created = client.post(
        f"/api/admin/model-configs/{cid}/routes",
        json={"display_name": "m1", "model_name": "m1", "priority": 10, "is_enabled": True},
        headers=_h(),
    ).json()

    async def healthy_stream(*_args):
        yield "OK"

    monkeypatch.setattr("app.api.admin_model_configs.stream_chat", healthy_stream)
    response = client.post(f"/api/admin/model-configs/{cid}/routes/test", headers=_h())
    assert response.status_code == 200
    assert response.json()["results"] == [{"route_id": created["id"], "model_name": "m1", "ok": True}]
    route = db_session.query(ModelRoute).filter(ModelRoute.id == created["id"]).one()
    assert route.health_status == "healthy"
    assert route.last_success_at is not None
    listed = client.get(f"/api/admin/model-configs/{cid}/routes", headers=_h()).json()
    assert listed[0]["connectivity_status"] == "passed"


def test_connectivity_marks_unactivated_product_failed_and_continues(
    client,
    db_session,
    monkeypatch,
):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "chain", "base_url": "https://x", "api_key": "key", "model_name": "m", "is_default": True},
        headers=_h(),
    ).json()["id"]
    created = []
    for index, model_name in enumerate(("not-activated", "available"), start=1):
        created.append(
            client.post(
                f"/api/admin/model-configs/{cid}/routes",
                json={
                    "display_name": model_name,
                    "model_name": model_name,
                    "priority": index * 10,
                    "is_enabled": True,
                },
                headers=_h(),
            ).json()
        )

    async def model_stream(_base_url, _api_key, model_name, _messages):
        if model_name == "not-activated":
            if False:
                yield ""
            raise LLMError(
                classify_provider_error(
                    400,
                    {
                        "code": "InvalidParameter",
                        "message": "The product is not activated, please activate it first",
                    },
                    model_name=model_name,
                )
            )
        yield "OK"

    monkeypatch.setattr("app.api.admin_model_configs.stream_chat", model_stream)
    response = client.post(f"/api/admin/model-configs/{cid}/routes/test", headers=_h())

    assert response.status_code == 200
    payload = response.json()
    assert [item["ok"] for item in payload["results"]] == [False, True]
    assert payload["stopped_early"] is False
    listed = client.get(f"/api/admin/model-configs/{cid}/routes", headers=_h()).json()
    assert listed[0]["health_status"] == "misconfigured"
    assert listed[0]["connectivity_status"] == "failed"
    assert listed[1]["connectivity_status"] == "passed"


def test_changing_model_id_resets_permanent_route_failure(client, db_session):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "chain", "base_url": "https://x", "api_key": "key", "model_name": "m", "is_default": True},
        headers=_h(),
    ).json()["id"]
    created = client.post(
        f"/api/admin/model-configs/{cid}/routes",
        json={"display_name": "old", "model_name": "old", "priority": 10, "is_enabled": True},
        headers=_h(),
    ).json()
    route = db_session.query(ModelRoute).filter(ModelRoute.id == created["id"]).one()
    route.health_status = "misconfigured"
    route.failure_streak = 2
    route.last_error_code = "ModelNotFound"
    route.last_error_message = "old model missing"
    route.last_success_at = route.created_at
    db_session.commit()

    response = client.put(
        f"/api/admin/model-configs/{cid}/routes/{route.id}",
        json={"display_name": "new", "model_name": "new", "priority": 10, "is_enabled": True},
        headers=_h(),
    )
    assert response.status_code == 200
    assert response.json()["health_status"] == "healthy"
    assert response.json()["failure_streak"] == 0
    assert response.json()["last_error_code"] is None
    assert response.json()["connectivity_status"] == "untested"


def test_batch_connectivity_reports_skipped_models_after_shared_auth_error(
    client,
    monkeypatch,
):
    cid = client.post(
        "/api/admin/model-configs",
        json={"name": "chain", "base_url": "https://x", "api_key": "key", "model_name": "m", "is_default": True},
        headers=_h(),
    ).json()["id"]
    for index in range(2):
        client.post(
            f"/api/admin/model-configs/{cid}/routes",
            json={"display_name": f"m{index}", "model_name": f"m{index}", "priority": index + 1, "is_enabled": True},
            headers=_h(),
        )

    async def invalid_key(*_args):
        if False:
            yield ""
        raise LLMError(classify_provider_error(401, {"code": "InvalidApiKey"}))

    monkeypatch.setattr("app.api.admin_model_configs.stream_chat", invalid_key)
    response = client.post(f"/api/admin/model-configs/{cid}/routes/test", headers=_h())
    payload = response.json()
    assert payload["total_enabled"] == 2
    assert payload["tested_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["stopped_early"] is True
    assert payload["stop_category"] == "credential_auth"
