"""用户认证接口（登录/登出/当前用户/改密）。"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    token = create_access_token(user.id, user.username, user.role)
    return {"access_token": token, "token_type": "bearer", "user": _user_dict(user)}


@router.post("/logout")
def logout(current: User = Depends(get_current_user)):
    # JWT 无状态，登出由前端清除 token；后端仅返回确认
    return {"message": "已登出"}


@router.get("/me")
def me(current: User = Depends(get_current_user)):
    return _user_dict(current)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    current: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.old_password, current.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="旧密码错误")
    current.password_hash = hash_password(body.new_password)
    db.add(current)
    db.commit()
    return {"message": "密码修改成功，请重新登录"}


def _user_dict(user: User) -> dict:
    return {"user_id": user.id, "username": user.username, "role": user.role}
