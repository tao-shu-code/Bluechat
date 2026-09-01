"""关键词检索通路（Task 7）：PostgreSQL 全文检索（同步 SQLAlchemy，原生 SQL）。

- 检索 langchain_pg_embedding（langchain-postgres 表结构），join documents 校验
  文档仍为 READY 且 kb_id 在可见集合内，join langchain_pg_collection 限定 collection；
- SQL 用 plainto_tsquery 解析用户输入（即 to_tsquery 的安全包装，避免布尔语法错误），
  配置用 settings.FTS_CONFIG，不存在时运行时降级 'simple'（查 pg_ts_config 并缓存）；
- ts_rank 排序 Top-K，返回与向量通路同构的结果。
"""

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.log import get_logger
from app.embedding.vector_store import collection_name_for

logger = get_logger(__name__)

# FTS 配置可用性缓存（进程内）：{配置名: 是否存在于 pg_ts_config}
_KNOWN_TS_CONFIGS: dict[str, bool] = {}

_SQL = sql_text(
    """
    SELECT e.cmetadata AS metadata,
           e.document  AS content,
           ts_rank(
               to_tsvector(CAST(:fts_config AS regconfig), COALESCE(e.document, '')),
               q.query
           ) AS rank
    FROM langchain_pg_embedding e
    JOIN langchain_pg_collection c ON c.uuid = e.collection_id
    JOIN documents d ON d.id = CAST(e.cmetadata->>'document_id' AS uuid),
    plainto_tsquery(CAST(:fts_config AS regconfig), :query) AS q (query)
    WHERE c.name = ANY(CAST(:collection_names AS text[]))
      AND d.status = 'READY'
      AND d.kb_id = ANY(CAST(:kb_ids AS uuid[]))
      AND to_tsvector(CAST(:fts_config AS regconfig), COALESCE(e.document, '')) @@ q.query
    ORDER BY rank DESC
    LIMIT :limit
    """
)


def resolve_fts_config(db: Session) -> str:
    """解析可用的全文检索配置：settings.FTS_CONFIG 不存在时降级 'simple'（结果缓存）。"""
    configured = settings.FTS_CONFIG
    if configured in _KNOWN_TS_CONFIGS:
        return configured if _KNOWN_TS_CONFIGS[configured] else "simple"
    available = (
        db.execute(
            sql_text("SELECT 1 FROM pg_ts_config WHERE cfgname = :name"),
            {"name": configured},
        ).first()
        is not None
    )
    _KNOWN_TS_CONFIGS[configured] = available
    if not available:
        logger.warning("text search config %r not found, fallback to 'simple'", configured)
        return "simple"
    return configured


def keyword_search(
    db: Session, query: str, kb_ids: list[str], *, top_k: int | None = None
) -> list[dict]:
    """全文检索召回：按 ts_rank 降序返回前 top_k 条。

    参数 kb_ids 必须是调用方按 get_visible_kb_ids 过滤后的可见集合；
    返回 [{"content", "metadata", "score"}]，score 为 ts_rank 原始值
    （RRF 仅依赖排名，不做归一化）。
    执行失败（如历史 chinese 配置引用的解析器库缺失）时自动以 simple 重试。
    """
    top_k = top_k or settings.RETRIEVAL_TOP_K
    if not query or not query.strip() or not kb_ids:
        return []

    fts_config = resolve_fts_config(db)
    try:
        rows = _execute_search(db, fts_config, query, kb_ids, top_k)
    except Exception as exc:
        if fts_config == "simple":
            logger.warning("keyword search failed: %s", exc)
            return []
        logger.warning(
            "keyword search with config %r failed (%s), retry with 'simple'",
            fts_config,
            exc,
        )
        try:
            rows = _execute_search(db, "simple", query, kb_ids, top_k)
        except Exception as exc2:
            logger.warning("keyword search (simple) failed: %s", exc2)
            return []

    results: list[dict] = []
    for metadata, content, rank in rows:
        results.append(
            {
                "content": content or "",
                "metadata": dict(metadata) if metadata else {},
                "score": float(rank),
            }
        )
    return results


def _execute_search(
    db: Session, fts_config: str, query: str, kb_ids: list[str], top_k: int
):
    """执行 FTS 查询并返回原始行。"""
    return db.execute(
        _SQL,
        {
            "fts_config": fts_config,
            "query": query,
            "collection_names": [collection_name_for(kb_id) for kb_id in kb_ids],
            "kb_ids": kb_ids,
            "limit": top_k,
        },
    ).all()
