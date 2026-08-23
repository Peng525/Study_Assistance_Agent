"""管理台模型配置 CRUD（api_key AES-GCM 加密 + mask 显示）。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.security import decrypt_api_key, encrypt_api_key
from app.models.models import ModelConfig, User

router = APIRouter(prefix="/api/admin/model-configs", tags=["admin-model-configs"])


class ModelConfigIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=256)
    api_key: str = Field(default="", max_length=512)  # 编辑时留空=不修改
    model_name: str = Field(min_length=1, max_length=128)
    is_default: bool = False


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"sk-****{key[-4:]}"


def _serialize(cfg: ModelConfig) -> dict:
    return {
        "id": cfg.id,
        "name": cfg.name,
        "base_url": cfg.base_url,
        "api_key_masked": _mask_key(decrypt_api_key(cfg.api_key_encrypted)),
        "model_name": cfg.model_name,
        "is_default": cfg.is_default,
    }


@router.get("")
def list_configs(current: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [_serialize(c) for c in db.query(ModelConfig).order_by(ModelConfig.id).all()]


@router.post("")
def create_config(body: ModelConfigIn, current: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not body.api_key:
        raise HTTPException(status_code=400, detail="api_key 不能为空")
    if body.is_default:
        _clear_default(db)
    cfg = ModelConfig(
        name=body.name,
        base_url=body.base_url,
        api_key_encrypted=encrypt_api_key(body.api_key),
        model_name=body.model_name,
        is_default=body.is_default,
    )
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return _serialize(cfg)


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
    return _serialize(cfg)


@router.delete("/{config_id}")
def delete_config(config_id: int, current: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    db.delete(cfg)
    db.commit()
    return {"message": "已删除"}


def _clear_default(db: Session):
    """将其他配置的 is_default 置 False（保证默认唯一）。"""
    db.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).update({"is_default": False})


def get_default_config(db: Session) -> ModelConfig | None:
    """获取默认模型配置（AI 对话调用）。"""
    return db.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).first()
