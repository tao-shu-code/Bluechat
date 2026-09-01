"""会话管理模块（Task 10）：会话 CRUD 接口与供 QA 模块复用的内部函数。

HTTP 接口（全部需登录）：
- POST   /api/conversations          创建会话（默认标题"新会话"）
- GET    /api/conversations          当前用户会话列表（updated_at 倒序，附最后一条消息摘要）
- GET    /api/conversations/{id}     会话详情 + 消息列表（按时间正序，sources 原样返回）
- DELETE /api/conversations/{id}     删除会话（级联消息）并清理 Redis 上下文缓存

内部函数（非 HTTP，供 QA 问答模块复用）：
- save_message(db, conversation_id, role, content, sources=None)
- get_history(conversation_id)
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.common.deps import CurrentUser, DBSession
from app.common.response import ok
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.log import get_logger
from app.models import Conversation, Message, MessageRole, User
from app.session import redis_cache

logger = get_logger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

DEFAULT_TITLE = "新会话"
_LAST_MESSAGE_SUMMARY_LEN = 100


class CreateConversationRequest(BaseModel):
    """创建会话请求体（title 可选，留空使用默认标题）。"""

    title: str | None = Field(default=None, max_length=255)


def _role_str(role: MessageRole | str) -> str:
    """统一消息角色字符串（MessageRole -> 枚举值）。"""
    return role.value if isinstance(role, MessageRole) else str(role)


def _conversation_payload(conv: Conversation) -> dict:
    """会话响应体。"""
    return {
        "id": str(conv.id),
        "title": conv.title,
        "created_at": conv.created_at.isoformat() if conv.created_at else None,
        "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
    }


def _message_payload(msg: Message) -> dict:
    """消息响应体（sources jsonb 原样返回；tokens 全空时为 None）。"""
    tokens = None
    if msg.total_tokens is not None or msg.prompt_tokens is not None:
        tokens = {
            "prompt_tokens": msg.prompt_tokens,
            "completion_tokens": msg.completion_tokens,
            "total_tokens": msg.total_tokens,
        }
    return {
        "id": str(msg.id),
        "role": _role_str(msg.role),
        "content": msg.content,
        "sources": msg.sources,
        "tokens": tokens,
        "feedback": msg.feedback,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


def _last_message_summary(msg: Message | None) -> dict | None:
    """最后一条消息摘要（内容截断，供会话列表展示）。"""
    if msg is None:
        return None
    content = msg.content or ""
    if len(content) > _LAST_MESSAGE_SUMMARY_LEN:
        content = content[:_LAST_MESSAGE_SUMMARY_LEN] + "..."
    return {"role": _role_str(msg.role), "content": content}


def _get_owned_conversation(db: Session, conversation_id: str, user: User) -> Conversation:
    """按 ID 取当前用户的会话；不存在 404，非本人 403。"""
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在") from None
    conv = db.get(Conversation, conv_uuid)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if conv.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该会话")
    return conv


@router.post("")
def create_conversation(
    db: DBSession,
    user: CurrentUser,
    body: CreateConversationRequest | None = None,
) -> dict:
    """创建会话（默认标题"新会话"），返回会话对象。"""
    title = (body.title.strip() if body and body.title else "") or DEFAULT_TITLE
    conv = Conversation(user_id=user.id, title=title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ok(_conversation_payload(conv))


@router.get("")
def list_conversations(
    db: DBSession,
    user: CurrentUser,
    page: int = 1,
    size: int = 10,
) -> dict:
    """当前用户的会话列表（按 updated_at 倒序，附最后一条消息摘要 last_message）。"""
    page = max(page, 1)
    size = min(max(size, 1), 100)
    total = db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user.id)
    ) or 0
    convs = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    items = []
    for conv in convs:
        last = db.scalars(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        ).first()
        items.append({**_conversation_payload(conv), "last_message": _last_message_summary(last)})
    return ok({"items": items, "total": total, "page": page, "size": size})


@router.get("/{conversation_id}")
def get_conversation_detail(
    conversation_id: str,
    db: DBSession,
    user: CurrentUser,
) -> dict:
    """会话详情 + 消息列表（按时间正序，sources jsonb 原样返回）。"""
    conv = _get_owned_conversation(db, conversation_id, user)
    messages = db.scalars(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
    ).all()
    return ok({**_conversation_payload(conv), "messages": [_message_payload(m) for m in messages]})


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    db: DBSession,
    user: CurrentUser,
) -> dict:
    """删除会话（级联删除消息）并清理 Redis 上下文缓存；非本人 403。"""
    conv = _get_owned_conversation(db, conversation_id, user)
    conv_id = str(conv.id)
    db.execute(delete(Message).where(Message.conversation_id == conv.id))
    db.delete(conv)
    db.commit()
    redis_cache.clear(conv_id)
    return ok({"id": conv_id})


# ---------- 内部函数（供 QA 模块复用，不作为 HTTP 接口） ----------


def save_message(
    db: Session,
    conversation_id: str | uuid.UUID,
    role: MessageRole | str,
    content: str,
    sources: dict | list | None = None,
    usage: dict | None = None,
) -> Message:
    """写入一条消息：落库 + 同步 Redis 上下文缓存 + 刷新会话 updated_at。

    usage 为 LLM token 用量 {"prompt_tokens", "completion_tokens", "total_tokens"}，
    仅 assistant 消息携带；供 QA 问答链路复用；Redis 故障时仅告警，不影响消息落库。
    """
    conv_uuid = (
        conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
    )
    role_value = _role_str(role)
    msg = Message(
        conversation_id=conv_uuid,
        role=MessageRole(role_value),
        content=content,
        sources=sources,
        prompt_tokens=usage.get("prompt_tokens") if usage else None,
        completion_tokens=usage.get("completion_tokens") if usage else None,
        total_tokens=usage.get("total_tokens") if usage else None,
    )
    db.add(msg)
    db.execute(
        update(Conversation).where(Conversation.id == conv_uuid).values(updated_at=func.now())
    )
    db.commit()
    db.refresh(msg)
    redis_cache.append_message(conv_uuid, role_value, content)
    return msg


def get_history(conversation_id: str | uuid.UUID) -> list[dict]:
    """获取会话历史（最近 HISTORY_ROUNDS 轮）：优先 Redis，miss 时查库并回填。

    返回按时间正序的 [{"role": ..., "content": ...}, ...]；
    Redis 不可用或无缓存时自动降级查库。
    """
    conv_uuid = (
        conversation_id if isinstance(conversation_id, uuid.UUID) else uuid.UUID(str(conversation_id))
    )
    cached = redis_cache.get_context(conv_uuid)
    if cached:
        return cached
    limit = settings.HISTORY_ROUNDS * 2
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(Message)
            .where(Message.conversation_id == conv_uuid)
            .order_by(Message.created_at.desc())
            .limit(limit)
        ).all()
    finally:
        db.close()
    rows.reverse()
    history = [{"role": _role_str(m.role), "content": m.content} for m in rows]
    # 回填 Redis（尽力而为，失败仅告警）
    redis_cache.fill_context(conv_uuid, history)
    return history
