"""认证领域服务：登录校验与用户信息序列化。"""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.security import verify_password
from app.models import User


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """按用户名查用户并校验密码；用户不存在、已禁用或密码错误均返回 None。"""
    user = db.scalars(
        select(User)
        .options(selectinload(User.user_roles))
        .where(User.username == username)
    ).first()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def user_payload(user: User) -> dict:
    """当前用户信息序列化（角色为去重排序后的 code 列表）。"""
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "roles": sorted({ur.role.code for ur in user.user_roles}),
    }
