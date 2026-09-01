"""混合检索编排（Task 7）：可见 KB 过滤 → 向量/关键词双路 → RRF 融合 → 可选 Rerank。

供 QA 模块调用（不新增路由）；返回结构：
{"chunks": [...], "max_similarity": float}
- chunks：Rerank（或降级后 RRF）排序的 [{content, metadata, score, vector_score, keyword_score, ...}]；
- max_similarity：向量路归一化相似度的最大值（0.0 表示无结果），
  供 QA 模块与 settings.RELEVANCE_THRESHOLD 比较做拒答判断。
"""

import time

from langsmith import traceable
from sqlalchemy.orm import Session

from app.common.deps import get_visible_kb_ids
from app.core.config import settings
from app.core.log import get_logger
from app.models import User
from app.retrieval.bm25 import bm25_search, ensure_bm25_index
from app.retrieval.fusion import rrf_fuse
from app.retrieval.keyword_search import keyword_search
from app.retrieval.reranker import rerank
from app.retrieval.vector_search import vector_search

logger = get_logger(__name__)


@traceable(name="retrieval.keyword_bm25", run_type="retriever", hide_inputs=["db"])
def keyword_results_for(db: Session, query: str, kb_ids: list[str], top_k: int) -> list[dict]:
    """关键词通路：优先 pg_search BM25，不可用时回退 zhparser/simple FTS。"""
    try:
        if ensure_bm25_index(db):
            results = bm25_search(db, query, kb_ids, top_k=top_k)
            if results is not None:
                return results
    except Exception as exc:  # BM25 任何异常都回退 FTS，不阻断检索
        logger.warning("bm25 path failed, fallback to FTS: %s", exc)
    return keyword_search(db, query, kb_ids, top_k=top_k)


@traceable(name="retrieval.hybrid_search", run_type="chain", hide_inputs=["db", "user"])
def hybrid_search(
    db: Session,
    user: User,
    query: str,
    kb_ids: list[str] | None = None,
    top_n: int | None = None,
) -> dict:
    """混合检索主入口。

    - kb_ids=None 时检索用户全部可见 KB；提供时与可见集合求交集（不越权）；
    - 向量路召回 settings.VECTOR_TOP_K、BM25/关键词路召回 settings.KEYWORD_TOP_K，
      RRF 融合后截断到 top_n（默认 settings.RERANK_TOP_N）；
    - Rerank 开启时对融合结果重排，失败自动降级为融合排序；
    - 每路耗时打点日志。
    """
    visible = get_visible_kb_ids(db, user)
    if kb_ids:
        allowed = set(visible)
        target_ids = [kb_id for kb_id in kb_ids if kb_id in allowed]
    else:
        target_ids = visible
    if not query or not query.strip() or not target_ids:
        return {"chunks": [], "max_similarity": 0.0}

    started = time.perf_counter()
    vector_results = vector_search(query, target_ids, top_k=settings.VECTOR_TOP_K)
    vector_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    keyword_results = keyword_results_for(
        db, query, target_ids, settings.KEYWORD_TOP_K
    )
    keyword_ms = (time.perf_counter() - started) * 1000

    logger.info(
        "hybrid retrieval timings vector=%.1fms keyword=%.1fms "
        "vector_hits=%s keyword_hits=%s kbs=%s",
        vector_ms,
        keyword_ms,
        len(vector_results),
        len(keyword_results),
        len(target_ids),
    )

    fused = rrf_fuse(vector_results, keyword_results)
    max_similarity = max((hit["score"] for hit in vector_results), default=0.0)

    final_n = top_n or settings.RERANK_TOP_N
    chunks = rerank(query, fused, top_n=final_n)
    return {"chunks": chunks, "max_similarity": float(max_similarity)}
