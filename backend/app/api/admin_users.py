"""管理台用户管理接口（列表 + 重置密码）。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.security import hash_password
from app.models.models import User

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users")
def list_users(
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.id).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    current: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    # 自锁防护：不能重置自己
    if user_id == current.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能重置自己的密码",
        )
    target = db.query(User).filter(User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    target.password_hash = hash_password("123456")
    db.add(target)
    db.commit()
    return {"message": f"已重置用户 {target.username} 的密码为默认值 123456"}
