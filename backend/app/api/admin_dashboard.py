"""管理台仪表盘统计接口。"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.models import ChatSession, Material, ModelConfig, User

router = APIRouter(prefix="/api/admin", tags=["admin-dashboard"])


@router.get("/stats")
def dashboard_stats(current: User = Depends(require_admin), db: Session = Depends(get_db)):
    """返回默认模型名 + 素材统计 + 最近 7 天会话数（柱状图数据）。"""
    default_cfg = db.query(ModelConfig).filter(ModelConfig.is_default.is_(True)).first()
    materials = db.query(Material).all()
    ready = sum(1 for m in materials if m.status == "ready")
    error = sum(1 for m in materials if m.status == "error")

    # 最近 7 天会话数
    now = datetime.now(timezone.utc)
    sessions = db.query(ChatSession).filter(ChatSession.created_at >= now - timedelta(days=7)).all()
    daily: dict[str, int] = {}
    for s in sessions:
        if s.created_at:
            day = s.created_at.strftime("%m-%d")
            daily[day] = daily.get(day, 0) + 1

    # 生成连续 7 天（含 0 值）
    last_7_days = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%m-%d")
        last_7_days.append({"date": d, "count": daily.get(d, 0)})

    return {
        "default_model_name": default_cfg.model_name if default_cfg else None,
        "material_total": len(materials),
        "material_ready": ready,
        "material_error": error,
        "user_total": db.query(User).count(),
        "last_7_days_sessions": last_7_days,
    }
