"""知识库管理接口（Task 3）：创建 / 列表 / 更新 / 删除。

- 创建/更新/删除：仅 ADMIN / KNOWLEDGE_MANAGER；
- 列表：按可见范围过滤（复用 app/common/deps.py 的 get_visible_kb_ids）；
- 创建/更新时写 kb_acl（visibility=ALL 时清空 ACL）；
- 删除时级联清理 MinIO 前缀与向量 collection（尽力而为）。
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.common.deps import CurrentUser, DBSession, get_visible_kb_ids, require_roles, user_role_codes
from app.common.exceptions import BizError
from app.common.response import ok
from app.core.log import get_logger
from app.document.minio_client import get_minio
from app.embedding.vector_store import drop_collection
from app.models import Department, KbAcl, KbVisibility, KnowledgeBase, User

logger = get_logger(__name__)

router = APIRouter(prefix="/api/kb", tags=["kb"])

KB_MANAGE_ROLES = ("ADMIN", "KNOWLEDGE_MANAGER")

# BizError 业务码约定：4001 参数错误 / 4004 资源不存在或不可见
CODE_PARAM_INVALID = 4001
CODE_KB_NOT_FOUND = 4004

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class KbUpsertRequest(BaseModel):
    """知识库创建/更新请求体。"""

    name: str
    description: str | None = None
    visibility: KbVisibility = KbVisibility.ALL
    department_ids: list[str] | None = None
    user_ids: list[str] | None = None


def _resolve_acl_targets(
    db: Session,
    visibility: KbVisibility,
    department_ids: list[str] | None,
    user_ids: list[str] | None,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """解析并校验 ACL 目标：visibility 为 DEPARTMENT/USER 时至少提供一个有效目标。"""
    dept_ids: list[uuid.UUID] = []
    user_id_list: list[uuid.UUID] = []

    for raw in department_ids or []:
        try:
            dept_ids.append(uuid.UUID(raw))
        except ValueError:
            raise BizError(CODE_PARAM_INVALID, f"无效的部门 ID：{raw}") from None
    for raw in user_ids or []:
        try:
            user_id_list.append(uuid.UUID(raw))
        except ValueError:
            raise BizError(CODE_PARAM_INVALID, f"无效的用户 ID：{raw}") from None

    if visibility == KbVisibility.ALL:
        return [], []

    if not dept_ids and not user_id_list:
        raise BizError(
            CODE_PARAM_INVALID, "visibility 为 DEPARTMENT/USER 时必须提供 department_ids 或 user_ids"
        )

    if dept_ids:
        found = set(db.scalars(select(Department.id).where(Department.id.in_(dept_ids))).all())
        missing = [str(i) for i in dept_ids if i not in found]
        if missing:
            raise BizError(CODE_PARAM_INVALID, f"部门不存在：{'、'.join(missing)}")
    if user_id_list:
        found = db.scalars(
            select(User.id).where(User.id.in_(user_id_list))
        ).all()
        missing = [str(i) for i in user_id_list if i not in set(found)]
        if missing:
            raise BizError(CODE_PARAM_INVALID, f"用户不存在：{'、'.join(missing)}")
    return dept_ids, user_id_list


def _replace_acl(
    db: Session,
    kb_id: uuid.UUID,
    visibility: KbVisibility,
    department_ids: list[uuid.UUID],
    user_ids: list[uuid.UUID],
) -> None:
    """重建 kb_acl：先清空，再按可见性写入（ALL 时不写任何 ACL）。"""
    db.execute(delete(KbAcl).where(KbAcl.kb_id == kb_id))
    if visibility == KbVisibility.ALL:
        return
    for dept_id in department_ids:
        db.add(KbAcl(kb_id=kb_id, department_id=dept_id))
    for user_id in user_ids:
        db.add(KbAcl(kb_id=kb_id, user_id=user_id))


def _kb_payload(db: Session, kb: KnowledgeBase) -> dict:
    """知识库响应体（含 ACL 目标列表）。"""
    acls = db.scalars(select(KbAcl).where(KbAcl.kb_id == kb.id)).all()
    return {
        "id": str(kb.id),
        "name": kb.name,
        "description": kb.description,
        "visibility": kb.visibility.value if hasattr(kb.visibility, "value") else kb.visibility,
        "department_ids": [str(a.department_id) for a in acls if a.department_id],
        "user_ids": [str(a.user_id) for a in acls if a.user_id],
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


def _get_kb_or_raise(db: Session, kb_id: str) -> KnowledgeBase:
    """按 ID 取知识库，不存在抛 BizError(4004)。"""
    try:
        kb_uuid = uuid.UUID(kb_id)
    except ValueError:
        raise BizError(CODE_KB_NOT_FOUND, "知识库不存在") from None
    kb = db.get(KnowledgeBase, kb_uuid)
    if kb is None:
        raise BizError(CODE_KB_NOT_FOUND, "知识库不存在")
    return kb


def _ensure_kb_manageable(db: Session, user: User, kb: KnowledgeBase) -> None:
    """非 ADMIN 的知识库管理员仅能管理其可见的知识库。"""
    if "ADMIN" in user_role_codes(user):
        return
    if str(kb.id) not in get_visible_kb_ids(db, user):
        raise BizError(CODE_KB_NOT_FOUND, "知识库不存在或不可见")


@router.post("")
def create_kb(
    body: KbUpsertRequest,
    db: DBSession,
    user: User = Depends(require_roles(*KB_MANAGE_ROLES)),
) -> dict:
    """创建知识库（写 kb_acl）。切分参数统一用全局配置。"""
    dept_ids, user_id_list = _resolve_acl_targets(db, body.visibility, body.department_ids, body.user_ids)

    kb = KnowledgeBase(
        name=body.name.strip(),
        description=body.description,
        visibility=body.visibility,
        created_by=user.id,
    )
    db.add(kb)
    db.flush()
    _replace_acl(db, kb.id, body.visibility, dept_ids, user_id_list)
    db.commit()
    db.refresh(kb)
    return ok(_kb_payload(db, kb))


@router.get("")
def list_kbs(db: DBSession, user: CurrentUser) -> dict:
    """知识库列表（按当前用户可见范围过滤）。"""
    visible_ids = set(get_visible_kb_ids(db, user))
    kbs = [kb for kb in db.scalars(select(KnowledgeBase)).all() if str(kb.id) in visible_ids]
    kbs.sort(key=lambda k: k.created_at or _EPOCH, reverse=True)
    return ok([_kb_payload(db, kb) for kb in kbs])


@router.put("/{kb_id}")
def update_kb(
    kb_id: str,
    body: KbUpsertRequest,
    db: DBSession,
    user: User = Depends(require_roles(*KB_MANAGE_ROLES)),
) -> dict:
    """更新知识库（可见性变化时重建 kb_acl）。"""
    kb = _get_kb_or_raise(db, kb_id)
    _ensure_kb_manageable(db, user, kb)

    dept_ids, user_id_list = _resolve_acl_targets(db, body.visibility, body.department_ids, body.user_ids)

    kb.name = body.name.strip()
    kb.description = body.description
    kb.visibility = body.visibility
    _replace_acl(db, kb.id, body.visibility, dept_ids, user_id_list)
    db.commit()
    db.refresh(kb)
    return ok(_kb_payload(db, kb))


@router.delete("/{kb_id}")
def delete_kb(
    kb_id: str,
    db: DBSession,
    user: User = Depends(require_roles(*KB_MANAGE_ROLES)),
) -> dict:
    """删除知识库（DB 级联删除 ACL/文档/分片），并尽力清理 MinIO 前缀与向量 collection。"""
    kb = _get_kb_or_raise(db, kb_id)
    _ensure_kb_manageable(db, user, kb)

    kb_uuid_str = str(kb.id)
    db.delete(kb)
    db.commit()

    # 尽力而为的级联清理（失败仅告警，不影响删除结果）
    try:
        deleted = get_minio().delete_prefix(f"{kb_uuid_str}/")
        logger.info("kb %s minio prefix cleaned, objects=%s", kb_uuid_str, deleted)
    except Exception as exc:
        logger.warning("kb %s minio cleanup failed: %s", kb_uuid_str, exc)
    try:
        drop_collection(kb_uuid_str)
    except Exception as exc:
        logger.warning("kb %s vector collection cleanup failed: %s", kb_uuid_str, exc)

    return ok({"id": kb_uuid_str})
