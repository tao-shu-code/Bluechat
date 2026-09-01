"""向量检索通路：单条 SQL 跨库相似度召回（pgvector）。

- 所有知识库的向量共居 langchain_pg_embedding 表（每库一个 collection 仅做
  逻辑隔离），检索用一条 SQL `WHERE collection_id IN (可见库)` 完成，
  **耗时与知识库数量无关**，权限过滤即 collection 白名单；
- query 只向量化一次（一次嵌入 API 调用）；
- 距离分归一化为 (0, 1] 的相似度（1/(1+d)，对余弦距离单调递减），
  供 RRF 调试与 RELEVANCE_THRESHOLD 拒答判断使用；
- 返回结构与关键词通路同构：[{"content", "metadata", "score"}]。
"""

from langsmith import traceable
from sqlalchemy import text as sql_text

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.log import get_logger
from app.embedding.vector_store import collection_name_for, get_embeddings

logger = get_logger(__name__)

_SQL_SEARCH = sql_text(
    """
    SELECT e.document              AS content,
           e.cmetadata             AS metadata,
           e.embedding <=> CAST(:query_vector AS vector) AS distance,
           c.name                  AS collection_name
    FROM langchain_pg_embedding e
    JOIN langchain_pg_collection c ON c.uuid = e.collection_id
    WHERE c.name = ANY(CAST(:collection_names AS text[]))
    ORDER BY e.embedding <=> CAST(:query_vector AS vector)
    LIMIT :top_k
    """
)


def distance_to_similarity(distance: float) -> float:
    """pgvector 距离 → (0, 1] 相似度（1/(1+d)，与具体距离度量无关的单调映射）。"""
    return 1.0 / (1.0 + max(float(distance), 0.0))


def _vector_literal(query_vector: list[float]) -> str:
    """向量列表 → pgvector 字面量 "[0.1,0.2,...]"（配合 ::vector 显式 cast）。"""
    return "[" + ",".join(f"{v:.6g}" for v in query_vector) + "]"


@traceable(name="retrieval.embed_query", run_type="embedding")
def _embed_query(query: str) -> list[float]:
    """query 向量化（独立追踪节点：供应商 API 调用，是检索延迟的主要来源）。"""
    return get_embeddings().embed_query(query)


@traceable(name="retrieval.vector_sql", run_type="retriever", hide_inputs=["query_vector"])
def _search_by_sql(query_vector: list[float], collection_names: list[str], top_k: int) -> list[dict]:
    """单条 SQL 跨库召回（独立追踪节点：纯 DB 耗时，正常毫秒级）。"""
    name_to_kb = {collection_name_for(kb): kb for kb in collection_names}
    db = SessionLocal()
    try:
        rows = db.execute(
            _SQL_SEARCH,
            {
                "query_vector": _vector_literal(query_vector),
                "collection_names": list(name_to_kb),
                "top_k": top_k,
            },
        ).mappings().all()
    finally:
        db.close()

    hits: list[dict] = []
    for row in rows:
        metadata = dict(row["metadata"] or {})
        metadata.setdefault("kb_id", name_to_kb.get(row["collection_name"]))
        hits.append(
            {
                "content": row["content"] or "",
                "metadata": metadata,
                "score": distance_to_similarity(row["distance"]),
            }
        )
    return hits


@traceable(name="retrieval.vector", run_type="retriever")
def vector_search(query: str, kb_ids: list[str], *, top_k: int | None = None) -> list[dict]:
    """跨库向量召回：query 一次向量化 → 单条 SQL 按可见 collection 白名单取全局 Top-K。

    参数 kb_ids 必须是调用方按 get_visible_kb_ids 过滤后的可见集合；
    查询失败（如表缺失 / 向量列异常）仅告警并返回空，不阻断混合检索。
    追踪树：retrieval.vector = retrieval.embed_query（供应商 API）+
    retrieval.vector_sql（纯 DB），耗时分布一目了然。
    """
    top_k = top_k or settings.RETRIEVAL_TOP_K
    if not query or not kb_ids:
        return []

    try:
        query_vector = _embed_query(query)
    except Exception as exc:
        logger.warning("query embedding failed, vector path skipped: %s", exc)
        return []

    try:
        return _search_by_sql(query_vector, kb_ids, top_k)
    except Exception as exc:
        logger.warning("vector search failed (single-sql path): %s", exc)
        return []
