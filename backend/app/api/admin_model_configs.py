"""管理台模型配置和优先级模型链管理。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.security import decrypt_api_key, encrypt_api_key
from app.models.models import ModelConfig, ModelRoute, SystemSetting, User
from app.services.llm_client import LLMError, stream_chat
from app.services.llm_errors import normalize_connectivity_error
from app.services.model_router import (
    ensure_route_chain_marker,
    initialize_route_preset,
    list_routes,
    record_route_failure,
    record_route_success,
    reset_route_state,
    reset_routes_for_config,
    routing_mode_key,
    serialize_route,
)

router = APIRouter(prefix="/api/admin/model-configs", tags=["admin-model-configs"])


class ModelConfigIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=256)
    api_key: str = Field(default="", max_length=512)  # 编辑时留空=不修改
    model_name: str = Field(min_length=1, max_length=128)
    is_default: bool = False


class ModelRouteIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=128)
    priority: int = Field(ge=1, le=10000)
    is_enabled: bool = True


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"sk-****{key[-4:]}"


def _serialize(cfg: ModelConfig, db: Session) -> dict:
    return {
        "id": cfg.id,
        "name": cfg.name,
        "base_url": cfg.base_url,
        "api_key_masked": _mask_key(decrypt_api_key(cfg.api_key_encrypted)),
        "model_name": cfg.model_name,
        "is_default": cfg.is_default,
        "route_count": db.query(ModelRoute).filter(ModelRoute.model_config_id == cfg.id).count(),
    }


@router.get("")
def list_configs(current: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_serialize(c, db) for c in db.query(ModelConfig).order_by(ModelConfig.id.asc()).all()]


@router.post("")
def create_config(body: ModelConfigIn, current: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not body.api_key:
        raise HTTPException(status_code=400, detail="api_key 不能为空")
    is_first_config = db.query(ModelConfig.id).first() is None
    is_default = body.is_default or is_first_config
    if is_default:
        _clear_default(db)
    cfg = ModelConfig(
        name=body.name,
        base_url=body.base_url,
        api_key_encrypted=encrypt_api_key(body.api_key),
        model_name=body.model_name,
        is_default=is_default,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return _serialize(cfg, db)


@router.put("/{config_id}")
def update_config(
    config_id: int,
    body: ModelConfigIn,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cfg = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    if cfg.is_default and not body.is_default:
        raise HTTPException(status_code=400, detail="必须保留一个默认 API，请先将其他接入设为默认")
    credentials_changed = cfg.base_url != body.base_url or bool(body.api_key)
    cfg.name = body.name
    cfg.base_url = body.base_url
    cfg.model_name = body.model_name
    # api_key 留空 = 不修改
    if body.api_key:
        cfg.api_key_encrypted = encrypt_api_key(body.api_key)
    if body.is_default:
        _clear_default(db)
        cfg.is_default = True
    else:
        cfg.is_default = False
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    if credentials_changed:
        reset_routes_for_config(db, cfg.id)
    return _serialize(cfg, db)


def _get_config_or_404(db: Session, config_id: int) -> ModelConfig:
    cfg = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    return cfg


def _get_route_or_404(db: Session, config_id: int, route_id: int) -> ModelRoute:
    route = (
        db.query(ModelRoute)
        .filter(ModelRoute.id == route_id, ModelRoute.model_config_id == config_id)
        .first()
    )
    if route is None:
        raise HTTPException(status_code=404, detail="模型路由不存在")
    return route


@router.get("/{config_id}/routes")
def get_routes(config_id: int, current: User = Depends(require_admin), db: Session = Depends(get_db)):
    _get_config_or_404(db, config_id)
    return [serialize_route(route) for route in list_routes(db, config_id)]


@router.post("/{config_id}/routes/presets/{preset_id}")
def import_route_preset(
    config_id: int,
    preset_id: str,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cfg = _get_config_or_404(db, config_id)
    try:
        routes = initialize_route_preset(db, cfg, preset_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="模型模板不存在") from None
    return [serialize_route(route) for route in routes]


@router.post("/{config_id}/routes")
def create_route(
    config_id: int,
    body: ModelRouteIn,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _get_config_or_404(db, config_id)
    # 在挂入新 route 前读取/创建 marker，避免查询触发 pending route 的自动 flush，
    # 使唯一约束错误能够统一由下面的 commit 捕获为 409。
    ensure_route_chain_marker(db, config_id)
    route = ModelRoute(model_config_id=config_id, **body.model_dump())
    db.add(route)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该模型已存在于当前模型链") from None
    db.refresh(route)
    return serialize_route(route)


@router.put("/{config_id}/routes/{route_id}")
def update_route(
    config_id: int,
    route_id: int,
    body: ModelRouteIn,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    route = _get_route_or_404(db, config_id, route_id)
    model_changed = route.model_name != body.model_name
    route.display_name = body.display_name
    route.model_name = body.model_name
    route.priority = body.priority
    route.is_enabled = body.is_enabled
    if model_changed:
        reset_route_state(route)
    db.add(route)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该模型已存在于当前模型链") from None
    db.refresh(route)
    return serialize_route(route)


@router.delete("/{config_id}/routes/{route_id}")
def delete_route(
    config_id: int,
    route_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    route = _get_route_or_404(db, config_id, route_id)
    db.delete(route)
    db.commit()
    return {"message": "已删除"}


@router.post("/{config_id}/routes/{route_id}/reset")
def reset_route(
    config_id: int,
    route_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    route = _get_route_or_404(db, config_id, route_id)
    reset_route_state(route)
    db.add(route)
    db.commit()
    db.refresh(route)
    return serialize_route(route)


@router.post("/{config_id}/routes/test")
async def test_routes(
    config_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cfg = _get_config_or_404(db, config_id)
    api_key = decrypt_api_key(cfg.api_key_encrypted)
    results = []
    enabled_routes = [item for item in list_routes(db, config_id) if item.is_enabled]
    stop_reason = None
    stop_category = None
    for route in enabled_routes:
        try:
            async with asyncio.timeout(15):
                async for _ in stream_chat(
                    cfg.base_url,
                    api_key,
                    route.model_name,
                    [{"role": "user", "content": "请只回复：OK"}],
                ):
                    pass
            record_route_success(db, route)
            results.append({"route_id": route.id, "model_name": route.model_name, "ok": True})
        except asyncio.TimeoutError:
            from app.services.llm_errors import LLMErrorCategory, local_provider_error

            error = local_provider_error(LLMErrorCategory.TIMEOUT, "connectivity test timeout")
            record_route_failure(db, config_id, route, error)
            results.append(
                {"route_id": route.id, "model_name": route.model_name, "ok": False, "error": error.user_message}
            )
        except LLMError as exc:
            error = normalize_connectivity_error(exc.details)
            can_continue = record_route_failure(db, config_id, route, error)
            results.append(
                {
                    "route_id": route.id,
                    "model_name": route.model_name,
                    "ok": False,
                    "error": error.user_message,
                }
            )
            if not can_continue:
                stop_reason = error.user_message
                stop_category = error.category.value
                break
    return {
        "results": results,
        "total_enabled": len(enabled_routes),
        "tested_count": len(results),
        "skipped_count": len(enabled_routes) - len(results),
        "stopped_early": len(results) < len(enabled_routes),
        "stop_reason": stop_reason,
        "stop_category": stop_category,
    }


@router.delete("/{config_id}")
def delete_config(config_id: int, current: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    was_default = cfg.is_default
    db.query(ModelRoute).filter(ModelRoute.model_config_id == cfg.id).delete(synchronize_session=False)
    db.query(SystemSetting).filter(SystemSetting.key == routing_mode_key(cfg.id)).delete(
        synchronize_session=False
    )
    db.delete(cfg)
    db.flush()
    if was_default:
        _clear_default(db)
        successor = db.query(ModelConfig).order_by(ModelConfig.id.asc()).first()
        if successor is not None:
            successor.is_default = True
            db.add(successor)
    db.commit()
    return {"message": "已删除"}


def _clear_default(db: Session):
    """将其他配置的 is_default 置 False（保证默认唯一）。"""
    db.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).update({"is_default": False})


def get_default_config(db: Session) -> ModelConfig | None:
    """获取默认模型配置（AI 对话调用）。"""
    return db.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).first()
