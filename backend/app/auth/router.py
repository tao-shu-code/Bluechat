"""认证与用户信息接口：登录 / 注册 / 登出 / 当前用户。"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.security import create_access_token, hash_password
from app.auth.service import authenticate_user, user_payload
from app.common.audit import audit_log
from app.common.deps import DBSession, CurrentUser, get_optional_current_user
from app.common.exceptions import BizError
from app.common.response import ok
from app.models import Role, User, UserRole

router = APIRouter(prefix="/api/auth", tags=["auth"])

# BizError 业务码约定：4001 参数错误 / 4002 业务冲突（如重名）/ 4004 资源不存在
CODE_PARAM_INVALID = 4001
CODE_DUPLICATE = 4002
CODE_NOT_FOUND = 4004

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD_LEN = 6


class LoginRequest(BaseModel):
    """登录请求体。"""

    username: str
    password: str


class RegisterRequest(BaseModel):
    """注册请求体（公开接口，新用户固定 EMPLOYEE 角色）。"""

    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=64)
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=128)


def _client_ip(request: Request) -> str | None:
    """客户端 IP（无连接信息时为 None）。"""
    return request.client.host if request.client else None


@router.post("/login")
def login(body: LoginRequest, request: Request, db: DBSession) -> dict:
    """登录校验通过后签发 JWT，并记录审计日志。"""
    user = authenticate_user(db, body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    token = create_access_token(user.id, user.username)
    audit_log(
        db,
        user.id,
        "login",
        detail={"username": user.username},
        ip=_client_ip(request),
    )
    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": user_payload(user),
        }
    )


@router.post("/register")
def register(body: RegisterRequest, request: Request, db: DBSession) -> dict:
    """用户自助注册：固定授予 EMPLOYEE 角色，成功后直接签发 JWT（免二次登录）。

    - 用户名 3~32 位，仅限字母/数字/_.-；
    - 密码至少 6 位；
    - 用户名唯一（重复返回业务码 4002）。
    """
    username = body.username.strip()
    if not _USERNAME_RE.fullmatch(username):
        raise BizError(
            CODE_PARAM_INVALID, "用户名需为 3~32 位字母、数字或 _. - 组成"
        )
    if len(body.password) < MIN_PASSWORD_LEN:
        raise BizError(CODE_PARAM_INVALID, "密码长度至少 6 位")

    exists = db.scalar(select(User.id).where(User.username == username))
    if exists:
        raise BizError(CODE_DUPLICATE, "用户名已存在")

    role = db.scalar(select(Role).where(Role.code == "EMPLOYEE"))
    if role is None:
        raise BizError(CODE_NOT_FOUND, "系统角色未初始化，请联系管理员")

    user = User(
        username=username,
        password_hash=hash_password(body.password),
        display_name=(body.display_name or "").strip() or username,
        email=(body.email or "").strip() or None,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()

    audit_log(
        db,
        user.id,
        "register",
        detail={"username": user.username},
        ip=_client_ip(request),
    )

    token = create_access_token(user.id, user.username)
    # user_payload 需要角色关系；注册事务已提交，重新加载角色
    db.refresh(user)
    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": user_payload(user),
        }
    )


@router.post("/logout")
def logout(
    request: Request,
    db: DBSession,
    user: User | None = Depends(get_optional_current_user),
) -> dict:
    """无状态 JWT 登出：仅记录审计日志（token 失效或缺失也返回成功）。"""
    audit_log(
        db,
        user.id if user else None,
        "logout",
        detail={"username": user.username} if user else None,
        ip=_client_ip(request),
    )
    return ok()


@router.get("/me")
def me(user: CurrentUser) -> dict:
    """返回当前登录用户信息（含角色）。"""
    return ok(user_payload(user))
