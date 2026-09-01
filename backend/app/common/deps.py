"""通用 FastAPI 依赖：当前用户认证、角色鉴权与知识库可见性计算。"""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth.security import decode_access_token
from app.core.database import get_db
from app.models import KbAcl, KbVisibility, KnowledgeBase, User

DBSession = Annotated[Session, Depends(get_db)]

# tokenUrl 指向登录接口（Swagger Authorize 调试入口）；缺失 token 由本模块统一抛 401
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    """构造 401 异常（带 WWW-Authenticate 头，响应结构由全局异常处理器统一）。"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _load_user(db: Session, user_id: uuid.UUID) -> User | None:
    """按 ID 加载启用用户，并预加载角色。"""
    return db.scalars(
        select(User)
        .options(selectinload(User.user_roles))
        .where(User.id == user_id, User.is_active.is_(True))
    ).first()


def _authenticate(db: Session, token: str | None) -> User:
    """解析 Bearer token 并加载用户；任何失败均抛 401。"""
    if not token:
        raise _unauthorized("未登录或缺少凭证")
    payload = decode_access_token(token)
    if payload is None:
        raise _unauthorized("无效或已过期的凭证")
    try:
        user_id = uuid.UUID(str(payload.get("sub")))
    except (TypeError, ValueError):
        raise _unauthorized("无效的凭证载荷") from None
    user = _load_user(db, user_id)
    if user is None:
        raise _unauthorized("用户不存在或已被禁用")
    return user


def get_current_user(
    token: Annotated[str | None, Depends(_oauth2_scheme)],
    db: DBSession,
) -> User:
    """依赖：从 Authorization: Bearer <token> 解析当前用户（含角色预加载）。"""
    return _authenticate(db, token)


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_optional_current_user(
    token: Annotated[str | None, Depends(_oauth2_scheme)],
    db: DBSession,
) -> User | None:
    """依赖：同 get_current_user，但凭证缺失/无效时返回 None（用于登出等弱认证场景）。"""
    if not token:
        return None
    try:
        return _authenticate(db, token)
    except HTTPException:
        return None


def user_role_codes(user: User) -> list[str]:
    """用户的角色 code 列表。"""
    return [ur.role.code for ur in user.user_roles]


def require_roles(*codes: str):
    """依赖工厂：要求当前用户拥有任一指定角色，否则 403；不传角色码时仅要求登录。"""
    required = set(codes)

    def dependency(user: CurrentUser) -> User:
        if not required or set(user_role_codes(user)) & required:
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足，需要以下角色之一：{'、'.join(codes)}",
        )

    return dependency


def get_visible_kb_ids(db: Session, user: User) -> list[str]:
    """按可见性规则计算用户可见的知识库 ID 列表（供检索/文档等模块复用）。

    规则：
    - visibility=ALL：所有用户可见；
    - visibility=DEPARTMENT：用户 department_id 命中 kb_acl.department_id 的知识库可见；
    - visibility=USER：用户 id 命中 kb_acl.user_id 的知识库可见；
    - 知识库创建者始终可见；
    - ADMIN 角色可见全部。
    """
    if "ADMIN" in user_role_codes(user):
        all_ids = db.scalars(select(KnowledgeBase.id)).all()
        return [str(kb_id) for kb_id in all_ids]

    stmt = select(KnowledgeBase.id).where(
        or_(
            KnowledgeBase.visibility == KbVisibility.ALL,
            KnowledgeBase.created_by == user.id,
            and_(
                KnowledgeBase.visibility == KbVisibility.DEPARTMENT,
                KnowledgeBase.id.in_(
                    select(KbAcl.kb_id).where(KbAcl.department_id == user.department_id)
                ),
            ),
            and_(
                KnowledgeBase.visibility == KbVisibility.USER,
                KnowledgeBase.id.in_(
                    select(KbAcl.kb_id).where(KbAcl.user_id == user.id)
                ),
            ),
        )
    )
    ids = db.scalars(stmt).all()
    return [str(kb_id) for kb_id in ids]
