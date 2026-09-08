"""Whisper 推理设备解析 / CUDA 可用性探测测试（2026-09-07）。

背景：本机 RTX 4070 上 `ctranslate2.get_cuda_device_count()` 返回 1，
但缺 `cublas64_12.dll`，真正推理时 CTranslate2 抛
"Library cublas64_12.dll is not found or cannot be loaded"，某些路径下还会**静默挂起**。

所以设备解析改成了：
  - `cpu`  → 短路返回，不做任何 GPU 探测
  - `cuda` → 真实探测；不可用即 **fail fast** 抛 RuntimeError（绝不静默退回 CPU）
  - `auto` → 能用 GPU 就用，不能就退回 CPU（不抛，这是 auto 的语义）

这组测试锁死上述三种语义，以及"管理台端点必须返回真实配置而非硬编码值"。
"""

import pytest

from app.core.config import settings
from app.services import whisper_service as ws


@pytest.fixture()
def gpu_unusable(monkeypatch):
    """让 GPU 探测返回不可用（真实机器上 GPU 可能是好的，必须 monkeypatch 才能测失败路径）。"""
    monkeypatch.setattr(
        ws,
        "_cuda_is_usable",
        lambda: (False, "CUDA 运行库缺失或无法加载：cublas64_12.dll"),
    )


@pytest.fixture()
def gpu_usable(monkeypatch):
    monkeypatch.setattr(ws, "_cuda_is_usable", lambda: (True, ""))


def test_resolve_device_cpu_skips_gpu_probe(monkeypatch):
    """显式 cpu 必须短路返回 —— 连探测都不该做（降级路径要绝对可靠）。"""
    called = []
    monkeypatch.setattr(ws, "_cuda_is_usable", lambda: called.append(1) or (False, "boom"))

    assert ws._resolve_device("cpu") == "cpu"
    assert called == [], "显式 cpu 不该触发 GPU 探测"


def test_resolve_device_cuda_fails_fast_when_gpu_unusable(gpu_unusable):
    """cuda 不可用必须 fail fast：抛异常 + 明确报出缺失依赖 + 给出降级办法。

    不能静默退回 CPU —— 那会让用户以为在用 GPU，实际以 1/6 速度跑完还毫不知情。
    """
    with pytest.raises(RuntimeError) as exc:
        ws._resolve_device("cuda")

    msg = str(exc.value)
    assert "cublas64_12.dll" in msg, "必须报出具体缺失的依赖，而不是笼统的『GPU 不可用』"
    assert "WHISPER_DEVICE=cpu" in msg, "必须告诉用户怎么降级"


def test_resolve_device_auto_falls_back_to_cpu_without_raising(gpu_unusable):
    """auto 的语义就是『能用就用，不能用就退』—— 不抛。"""
    assert ws._resolve_device("auto") == "cpu"


def test_resolve_device_returns_cuda_when_usable(gpu_usable):
    for requested in ("cuda", "auto"):
        assert ws._resolve_device(requested) == "cuda"


def test_register_cuda_dll_dirs_is_idempotent():
    """注册函数必须幂等 —— 它会在 _cuda_is_usable 和 _run_whisper 里被调用多次。"""
    first = ws._register_cuda_dll_dirs()
    second = ws._register_cuda_dll_dirs()
    assert first == second


def test_describe_runtime_reports_real_config(gpu_usable, monkeypatch):
    """管理台展示的是**实际解析值**，不是硬编码 —— 旧实现把 model 写死成 "medium"。"""
    monkeypatch.setattr(settings, "whisper_model_size", "small")
    monkeypatch.setattr(settings, "whisper_device", "cuda")
    monkeypatch.setattr(settings, "whisper_compute_type", "default")

    info = ws.describe_runtime()

    assert info["model"] == "small", "model 必须是真实配置，不能硬编码 medium"
    assert info["device"] == "cuda"
    assert info["compute_type"] == "float16", "GPU + default 应解析为 float16"
    assert info["cuda_usable"] is True


def test_describe_runtime_surfaces_reason_when_gpu_broken(gpu_unusable, monkeypatch):
    """GPU 坏了端点不能 500，必须把原因如实报出来。"""
    monkeypatch.setattr(settings, "whisper_device", "cuda")

    info = ws.describe_runtime()

    assert info["cuda_usable"] is False
    assert "cublas64_12.dll" in (info["cuda_reason"] or "")


@pytest.fixture()
def admin_client(db_session):
    """管理台客户端。**必须用 conftest 的 `db_session`（tmp 库）** ——
    曾经在这个测试里用 `SessionLocal()` 直连真实 `app.db` 建用户，
    导致第二次跑测试 `UNIQUE constraint failed: users.username`，还污染了开发库。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.admin_materials import router as materials_router
    from app.core.database import get_db
    from app.core.security import create_access_token, hash_password
    from app.models.models import User

    db_session.add(User(username="admin", password_hash=hash_password("123456"), role="admin"))
    db_session.commit()

    app = FastAPI()
    app.include_router(materials_router)
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {create_access_token(1, 'admin', 'admin')}"})
    return client


def test_model_status_endpoint_returns_real_values(admin_client, gpu_usable, monkeypatch):
    """`/whisper/model-status` 必须返回实际配置，并对旧字段保持向后兼容。"""
    monkeypatch.setattr(settings, "whisper_model_size", "small")

    resp = admin_client.get("/api/admin/materials/whisper/model-status")

    assert resp.status_code == 200
    body = resp.json()

    # 向后兼容：前端现在就在读这三个字段
    assert "ffmpeg_available" in body
    assert "active_tasks" in body
    # 关键：不再是硬编码的 "medium"
    assert body["model"] == "small"
    assert body["device"] == "cuda"
    assert body["compute_type"] == "float16"
    assert body["cuda_usable"] is True
