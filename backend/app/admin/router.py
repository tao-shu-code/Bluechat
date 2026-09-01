"""系统管理接口：用户 / 部门 / 角色管理（供权限管理界面使用，仅 ADMIN）。

- 全部接口通过路由级依赖 require_roles("ADMIN") 鉴权；
- 创建/更新用户写审计日志（action=admin_create_user / admin_update_user）；
- 不允许通过禁用 is_active 将当前登录账号自身停用（防止自锁）。
"""

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth.security import hash_password
from app.common.audit import audit_log
from app.common.deps import CurrentUser, DBSession, require_roles
from app.common.exceptions import BizError
from app.common.response import ok
from app.models import Department, Role, User, UserRole

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_roles("ADMIN"))],
)

# BizError 业务码约定：4001 参数错误 / 4002 业务冲突（如重名）/ 4004 资源不存在
CODE_PARAM_INVALID = 4001
CODE_DUPLICATE = 4002
CODE_NOT_FOUND = 4004


class UserCreateRequest(BaseModel):
    """创建用户请求体。"""

    username: str
    password: str
    display_name: str | None = None
    email: str | None = None
    department_id: str | None = None
    roles: list[str]


class UserUpdateRequest(BaseModel):
    """更新用户请求体（仅更新提供的字段）。"""

    display_name: str | None = None
    email: str | None = None
    department_id: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None


class DepartmentCreateRequest(BaseModel):
    """新建部门请求体。"""

    name: str
    parent_id: str | None = None


def _client_ip(request: Request) -> str | None:
    """客户端 IP（无连接信息时为 None）。"""
    return request.client.host if request.client else None


def _parse_uuid(raw: str, message: str) -> uuid.UUID:
    """解析 UUID 字符串，非法时抛参数错误。"""
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise BizError(CODE_PARAM_INVALID, message) from None


def _resolve_roles(db: Session, codes: list[str]) -> list[Role]:
    """按 code 查角色并校验全部存在，返回角色实体列表。"""
    unique = sorted(set(codes))
    roles = db.scalars(select(Role).where(Role.code.in_(unique))).all()
    found = {r.code for r in roles}
    missing = [code for code in unique if code not in found]
    if missing:
        raise BizError(CODE_PARAM_INVALID, f"角色不存在：{'、'.join(missing)}")
    return list(roles)


def _department_name_map(db: Session, department_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """批量查询部门名称映射。"""
    ids = [i for i in set(department_ids) if i is not None]
    if not ids:
        return {}
    rows = db.scalars(select(Department).where(Department.id.in_(ids))).all()
    return {d.id: d.name for d in rows}


def _user_payload(user: User, department_name: str | None = None) -> dict:
    """用户响应体（角色为去重排序后的 code 列表）。"""
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "department_id": str(user.department_id) if user.department_id else None,
        "department_name": department_name,
        "is_active": user.is_active,
        "roles": sorted({ur.role.code for ur in user.user_roles}),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _user_payload_with_dept(db: Session, user: User) -> dict:
    """用户响应体（含部门名称）。"""
    name_map = _department_name_map(db, [user.department_id])
    return _user_payload(user, name_map.get(user.department_id))


def _get_user_or_raise(db: Session, user_id: str) -> User:
    """按 ID 取用户（预加载角色），不存在抛 BizError(4004)。"""
    uid = _parse_uuid(user_id, "无效的用户 ID")
    user = db.scalars(
        select(User)
        .options(selectinload(User.user_roles))
        .where(User.id == uid)
    ).first()
    if user is None:
        raise BizError(CODE_NOT_FOUND, "用户不存在")
    return user


def _department_payload(dept: Department) -> dict:
    """部门响应体。"""
    return {
        "id": str(dept.id),
        "name": dept.name,
        "parent_id": str(dept.parent_id) if dept.parent_id else None,
    }


@router.get("/users")
def list_users(
    db: DBSession,
    page: int = 1,
    size: int = 20,
    keyword: str | None = None,
) -> dict:
    """用户分页列表（keyword 模糊匹配 username / display_name）。"""
    page = max(1, page)
    size = min(max(1, size), 100)

    conditions = []
    if keyword:
        like = f"%{keyword}%"
        conditions.append(or_(User.username.ilike(like), User.display_name.ilike(like)))

    total = db.scalar(select(func.count()).select_from(User).where(*conditions))
    rows = db.scalars(
        select(User)
        .options(selectinload(User.user_roles))
        .where(*conditions)
        .order_by(User.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    name_map = _department_name_map(db, [u.department_id for u in rows])
    items = [_user_payload(u, name_map.get(u.department_id)) for u in rows]
    return ok({"total": total or 0, "page": page, "size": size, "items": items})


@router.post("/users")
def create_user(
    body: UserCreateRequest,
    request: Request,
    db: DBSession,
    current: CurrentUser,
) -> dict:
    """创建用户（bcrypt 哈希密码，分配角色），并写审计日志。"""
    if not body.username.strip():
        raise BizError(CODE_PARAM_INVALID, "用户名不能为空")
    if not body.password:
        raise BizError(CODE_PARAM_INVALID, "密码不能为空")
    if not body.roles:
        raise BizError(CODE_PARAM_INVALID, "至少分配一个角色")

    if db.scalar(select(User.id).where(User.username == body.username)) is not None:
        raise BizError(CODE_DUPLICATE, f"用户名已存在：{body.username}")

    dept_id = None
    if body.department_id:
        dept_id = _parse_uuid(body.department_id, "无效的部门 ID")
        if db.scalar(select(Department.id).where(Department.id == dept_id)) is None:
            raise BizError(CODE_PARAM_INVALID, "部门不存在")

    roles = _resolve_roles(db, body.roles)

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        email=body.email,
        department_id=dept_id,
        is_active=True,
    )
    db.add(user)
    db.flush()
    user.user_roles = [UserRole(user_id=user.id, role_id=r.id) for r in roles]
    db.commit()
    db.refresh(user)

    audit_log(
        db,
        current.id,
        "admin_create_user",
        detail={"username": user.username, "roles": sorted({r.code for r in roles})},
        ip=_client_ip(request),
    )
    return ok(_user_payload_with_dept(db, user))


@router.put("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdateRequest,
    request: Request,
    db: DBSession,
    current: CurrentUser,
) -> dict:
    """更新用户（仅更新提供的字段；roles 提供时整体替换 user_roles）。"""
    user = _get_user_or_raise(db, user_id)
    fields = body.model_fields_set

    if "display_name" in fields:
        user.display_name = body.display_name
    if "email" in fields:
        user.email = body.email
    if "department_id" in fields:
        if body.department_id is None:
            user.department_id = None
        else:
            dept_id = _parse_uuid(body.department_id, "无效的部门 ID")
            if db.scalar(select(Department.id).where(Department.id == dept_id)) is None:
                raise BizError(CODE_PARAM_INVALID, "部门不存在")
            user.department_id = dept_id
    if "is_active" in fields and body.is_active is not None:
        if user.id == current.id and not body.is_active:
            raise BizError(CODE_PARAM_INVALID, "不允许禁用当前登录账号")
        user.is_active = body.is_active
    if "roles" in fields and body.roles is not None:
        roles = _resolve_roles(db, body.roles)
        user.user_roles = [UserRole(user_id=user.id, role_id=r.id) for r in roles]

    db.commit()
    db.refresh(user)

    audit_log(
        db,
        current.id,
        "admin_update_user",
        detail={"target_username": user.username, "fields": sorted(fields)},
        ip=_client_ip(request),
    )
    return ok(_user_payload_with_dept(db, user))


@router.get("/departments")
def list_departments(db: DBSession) -> dict:
    """部门列表（含父级 ID，按创建时间升序）。"""
    rows = db.scalars(select(Department).order_by(Department.created_at)).all()
    return ok([_department_payload(d) for d in rows])


@router.post("/departments")
def create_department(body: DepartmentCreateRequest, db: DBSession) -> dict:
    """新建部门（可指定上级部门）。"""
    name = body.name.strip()
    if not name:
        raise BizError(CODE_PARAM_INVALID, "部门名称不能为空")

    parent_id = None
    if body.parent_id:
        parent_id = _parse_uuid(body.parent_id, "无效的上级部门 ID")
        if db.scalar(select(Department.id).where(Department.id == parent_id)) is None:
            raise BizError(CODE_PARAM_INVALID, "上级部门不存在")

    dept = Department(name=name, parent_id=parent_id)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return ok(_department_payload(dept))


@router.get("/roles")
def list_roles(db: DBSession) -> dict:
    """角色列表（按 code 升序）。"""
    rows = db.scalars(select(Role).order_by(Role.code)).all()
    return ok([{"id": str(r.id), "code": r.code, "name": r.name} for r in rows])


# ---------- 问答记录与 Token 用量（管理员查看） ----------

_ANSWER_PREVIEW_LEN = 500


@router.get("/qa/summary")
def qa_summary(db: DBSession) -> dict:
    """问答 Token 用量与反馈汇总（assistant 消息为一次问答）。"""
    from datetime import date, datetime, time

    from app.models import Message, MessageRole

    assistant_role = MessageRole.assistant.value
    total_q, prompt_t, completion_t, total_t = db.execute(
        select(
            func.count(Message.id),
            func.coalesce(func.sum(Message.prompt_tokens), 0),
            func.coalesce(func.sum(Message.completion_tokens), 0),
            func.coalesce(func.sum(Message.total_tokens), 0),
        ).where(Message.role == assistant_role)
    ).one()
    today_start = datetime.combine(date.today(), time.min)
    today_tokens = db.scalar(
        select(func.coalesce(func.sum(Message.total_tokens), 0)).where(
            Message.role == assistant_role,
            Message.created_at >= today_start,
        )
    ) or 0
    like_count = db.scalar(
        select(func.count(Message.id)).where(
            Message.role == assistant_role, Message.feedback == "like"
        )
    ) or 0
    dislike_count = db.scalar(
        select(func.count(Message.id)).where(
            Message.role == assistant_role, Message.feedback == "dislike"
        )
    ) or 0
    return ok(
        {
            "total_questions": total_q or 0,
            "prompt_tokens": int(prompt_t or 0),
            "completion_tokens": int(completion_t or 0),
            "total_tokens": int(total_t or 0),
            "today_tokens": int(today_tokens or 0),
            "like_count": like_count or 0,
            "dislike_count": dislike_count or 0,
        }
    )


@router.get("/qa/records")
def qa_records(
    db: DBSession,
    page: int = 1,
    size: int = 10,
    feedback: str | None = None,
    keyword: str | None = None,
) -> dict:
    """全部问答记录（assistant 消息倒序分页，附提问内容与用户/会话信息）。"""
    from app.models import Conversation, Message, MessageRole, User

    page = max(page, 1)
    size = min(max(size, 1), 50)
    query = (
        select(
            Message,
            Conversation.title.label("conv_title"),
            User.username,
            User.display_name,
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .join(User, Conversation.user_id == User.id)
        .where(Message.role == MessageRole.assistant.value)
    )
    if feedback in ("like", "dislike"):
        query = query.where(Message.feedback == feedback)
    elif feedback:
        raise BizError(CODE_PARAM_INVALID, "feedback 仅支持 like / dislike")
    if keyword:
        keyword = keyword.strip()
        if keyword:
            query = query.where(Message.content.ilike(f"%{keyword}%"))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(Message.created_at.desc()).offset((page - 1) * size).limit(size)
    ).all()

    items = []
    for msg, conv_title, username, display_name in rows:
        # 关联提问：同会话中早于该回答的最近一条 user 消息
        prev = db.scalars(
            select(Message)
            .where(
                Message.conversation_id == msg.conversation_id,
                Message.role == MessageRole.user.value,
                Message.created_at <= msg.created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        ).first()
        answer = msg.content or ""
        if len(answer) > _ANSWER_PREVIEW_LEN:
            answer = answer[:_ANSWER_PREVIEW_LEN] + "..."
        items.append(
            {
                "message_id": str(msg.id),
                "conversation_title": conv_title,
                "username": username,
                "display_name": display_name,
                "question": prev.content if prev else None,
                "answer": answer,
                "tokens": {
                    "prompt_tokens": msg.prompt_tokens,
                    "completion_tokens": msg.completion_tokens,
                    "total_tokens": msg.total_tokens,
                },
                "feedback": msg.feedback,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
        )
    return ok({"items": items, "total": total, "page": page, "size": size})
