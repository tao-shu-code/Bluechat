"""存量库幂等迁移（无 Alembic，参照 ensure_hnsw 的"尽力而为"模式）。

启动时执行轻量 DDL：
- messages 表 token 用量与反馈列（qa token 统计 / 点赞点踩功能引入）；
- knowledge_bases 表删除 chunk_size / chunk_overlap 列（切分参数统一走全局配置）；
任一失败仅告警不阻断启动（新装库由 02_schema.sql 直接建全，无需迁移）。
"""

from sqlalchemy import text

from app.core.database import engine
from app.core.log import get_logger

logger = get_logger(__name__)

_MESSAGE_COLUMNS_SQL = [
    text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER"),
    text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS completion_tokens INTEGER"),
    text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS total_tokens INTEGER"),
    text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS feedback VARCHAR(16)"),
]

_KB_DROP_COLUMNS_SQL = [
    text("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS chunk_size"),
    text("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS chunk_overlap"),
]


def ensure_message_columns() -> None:
    """确保 messages 表具备 token / feedback 列（幂等，可重复执行）。"""
    with engine.begin() as conn:
        for stmt in _MESSAGE_COLUMNS_SQL:
            conn.execute(stmt)
    logger.info("message columns ensured (tokens/feedback)")


def drop_kb_chunk_columns() -> None:
    """删除 knowledge_bases 表的 chunk_size / chunk_overlap 列（幂等，可重复执行）。"""
    with engine.begin() as conn:
        for stmt in _KB_DROP_COLUMNS_SQL:
            conn.execute(stmt)
    logger.info("kb chunk columns dropped (chunk params now global-only)")
