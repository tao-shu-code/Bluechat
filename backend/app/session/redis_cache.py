"""Redis 会话上下文缓存封装。

key 形如 conv:{conversation_id}:context，list 结构按时间正序保存最近
HISTORY_ROUNDS * 2 条消息（每条为 JSON 字符串 {"role", "content"}）。

所有操作均为"尽力而为"：Redis 不可用时记录 warning 并降级
（读返回空列表、写静默跳过），由调用方回退数据库，不阻断业务。
"""

import json
from uuid import UUID

import redis

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger(__name__)

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """单例同步 Redis 客户端（懒加载；短超时便于故障时快速降级）。"""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _client


def _context_key(conversation_id: str | UUID) -> str:
    """会话上下文缓存 key。"""
    return f"conv:{conversation_id}:context"


def _max_messages() -> int:
    """上下文保留的最大消息条数（最近 N 轮 ≈ HISTORY_ROUNDS * 2 条消息）。"""
    return max(settings.HISTORY_ROUNDS * 2, 1)


def get_context(conversation_id: str | UUID) -> list[dict]:
    """读取会话上下文（无缓存或 Redis 不可用时返回空列表，即视为 miss）。"""
    try:
        raw_list = get_redis().lrange(_context_key(conversation_id), 0, -1)
    except Exception as exc:
        logger.warning("redis get_context failed, conv=%s: %s", conversation_id, exc)
        return []
    context: list[dict] = []
    for raw in raw_list:
        try:
            item = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("redis context dirty data skipped, conv=%s", conversation_id)
            continue
        if isinstance(item, dict):
            context.append(item)
    return context


def append_message(conversation_id: str | UUID, role: str, content: str) -> None:
    """追加一条消息到上下文缓存，超出上限时裁剪最旧消息。"""
    try:
        client = get_redis()
        key = _context_key(conversation_id)
        payload = json.dumps(
            {"role": role, "content": content}, ensure_ascii=False, default=str
        )
        pipe = client.pipeline()
        pipe.rpush(key, payload)
        pipe.ltrim(key, -_max_messages(), -1)
        pipe.execute()
    except Exception as exc:
        logger.warning("redis append_message failed, conv=%s: %s", conversation_id, exc)


def fill_context(conversation_id: str | UUID, messages: list[dict]) -> None:
    """整体重建上下文缓存（DEL + 批量 RPUSH，供 get_history 回填使用）。"""
    if not messages:
        return
    try:
        client = get_redis()
        key = _context_key(conversation_id)
        pipe = client.pipeline()
        pipe.delete(key)
        for item in messages:
            payload = json.dumps(
                {"role": item.get("role"), "content": item.get("content")},
                ensure_ascii=False,
                default=str,
            )
            pipe.rpush(key, payload)
        pipe.ltrim(key, -_max_messages(), -1)
        pipe.execute()
    except Exception as exc:
        logger.warning("redis fill_context failed, conv=%s: %s", conversation_id, exc)


def clear(conversation_id: str | UUID) -> None:
    """清除会话上下文缓存（Redis 不可用时仅告警）。"""
    try:
        get_redis().delete(_context_key(conversation_id))
    except Exception as exc:
        logger.warning("redis clear failed, conv=%s: %s", conversation_id, exc)
