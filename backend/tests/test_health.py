"""模块 0.1 后端骨架测试。"""

from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import app

client = TestClient(app)


def test_health_check():
    """测试健康检查接口返回 200 和正确状态。"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == settings.app_name


def test_unknown_route_returns_404():
    """测试未知路由返回 404。"""
    response = client.get("/nonexistent")
    assert response.status_code == 404


def test_settings_loads_from_env_file():
    """测试配置能从 .env 读取（含大模型 base_url 与管理员账号）。"""
    assert settings.app_name == "AI 助学助手"
    assert settings.llm_base_url.startswith("https://")
    assert settings.admin_username == "admin"
    assert settings.jwt_ttl_seconds == 3600


def test_settings_env_override(monkeypatch):
    """测试环境变量能覆盖默认值。"""
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("LLM_MODEL_NAME", "qwen-max")
    s = Settings(_env_file=None)
    assert s.debug is True
    assert s.llm_model_name == "qwen-max"
