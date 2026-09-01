"""pg_search 原生 BM25 检索（文档检索工具与 QA 关键词通路共用）。

- BM25 索引建在业务表 chunks(content) 上（key_field=id），与向量库表解耦；
- 中文分词优先 chinese_lindera，创建失败降级 ngram；仍失败则 bm25_available=False，
  调用方回退到 keyword_search（zhparser/simple FTS）；
- bm25_search 正常空结果返回 []；pg_search 不可用或查询失败返回 None（供调用方走回退）；
- 扩展与索引均幂等创建（存量库无需重跑 init SQL）。
"""

from langsmith import traceable
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger(__name__)

INDEX_NAME = "chunks_content_bm25"

# 进程内状态缓存
_state: dict = {"ensured": False, "available": False, "tokenizer": None}

_SQL_SEARCH = sql_text(
    """
    SELECT c.content,
           c.title_path,
           c.page_number,
           c.chunk_index,
           c.document_id,
           d.kb_id,
           paradedb.score(c.id) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.id @@@ paradedb.match('content', CAST(:query AS text))
      AND d.status = 'READY'
      AND d.kb_id = ANY(CAST(:kb_ids AS uuid[]))
    ORDER BY score DESC
    LIMIT :limit
    """
)

def _index_sql(tokenizer_json: str) -> str:
    """构造 chunks 表 BM25 索引 DDL（tokenizer_json 为 JSON 配置字符串）。"""
    return (
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} ON chunks USING bm25 (id, content) "
        f"WITH (key_field='id', text_fields='{tokenizer_json}')"
    )


# 两级分词降级：chinese_lindera（词典级中文分词）→ ngram（2~4 元，无需词典）
# 注意 text_fields 必须包含字段名（content）的完整配置包装
_TOKENIZERS = (
    ('chinese_lindera', '{"content":{"tokenizer":{"type":"chinese_lindera"}}}'),
    ('ngram', '{"content":{"tokenizer":{"type":"ngram","min_gram":2,"max_gram":4}}}'),
)


def _try_create_index(db: Session, name: str, tokenizer_json: str) -> bool:
    """按指定 tokenizer 尝试创建 BM25 索引，成功返回 True。"""
    try:
        db.execute(sql_text(_index_sql(tokenizer_json)))
        db.commit()
        return True
    except Exception as exc:
        logger.warning("create bm25 index (tokenizer=%s) failed: %s", name, exc)
        db.rollback()
        return False


def ensure_bm25_index(db: Session) -> bool:
    """幂等创建 pg_search 扩展与 chunks BM25 索引；返回 BM25 是否可用。"""
    if _state["ensured"]:
        return _state["available"]
    _state["ensured"] = True
    try:
        db.execute(sql_text("CREATE EXTENSION IF NOT EXISTS pg_search"))
        db.commit()
    except Exception as exc:
        logger.warning("create extension pg_search failed: %s", exc)
        db.rollback()
        return False

    for name, tokenizer_json in _TOKENIZERS:
        if _try_create_index(db, name, tokenizer_json):
            _state["available"] = True
            _state["tokenizer"] = name
            logger.info("bm25 index ready (tokenizer=%s)", name)
            break

    if not _state["available"]:
        logger.warning("bm25 index unavailable, keyword path will fallback to FTS")
    return _state["available"]


def bm25_available() -> bool:
    """BM25 是否已就绪（ensure_bm25_index 之后有意义）。"""
    return _state["available"]


@traceable(name="retrieval.bm25", run_type="retriever", hide_inputs=["db"])
def bm25_search(
    db: Session, query: str, kb_ids: list[str], *, top_k: int | None = None
) -> list[dict] | None:
    """pg_search BM25 检索。

    返回与向量通路同构的 [{"content", "metadata", "score"}]（score 为 BM25 相关性）；
    空结果返回 []；pg_search 不可用或查询异常返回 None（调用方走 FTS 回退）。
    """
    top_k = top_k or settings.RETRIEVAL_TOP_K
    if not query or not query.strip() or not kb_ids:
        return []
    if not _state["available"]:
        return None
    try:
        rows = db.execute(
            _SQL_SEARCH,
            {"query": query.strip(), "kb_ids": kb_ids, "limit": top_k},
        ).all()
    except Exception as exc:
        logger.warning("bm25 search failed (fallback to FTS): %s", exc)
        return None

    results: list[dict] = []
    for content, title_path, page_number, chunk_index, document_id, kb_id, score in rows:
        results.append(
            {
                "content": content or "",
                "metadata": {
                    "document_id": str(document_id),
                    "kb_id": str(kb_id),
                    "title_path": title_path,
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                },
                "score": float(score),
            }
        )
    return results
