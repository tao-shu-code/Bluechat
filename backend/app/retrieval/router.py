"""文档检索调试接口：BM25 + 向量双路召回，RRF 倒排排名融合后返回。

- POST /api/retrieval/search：kb_ids 与当前用户可见集合求交集，不越权；
- BM25 走 pg_search（不可用时内部回退 zhparser/simple FTS），向量走 PGVector；
- 融合 score = Σ 1/(k + rank_i)（k=60），并保留两路原始分数与排名；
- 结果按 document_id 批量补 document_name（filename）。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.common.deps import CurrentUser, DBSession, get_visible_kb_ids
from app.common.response import ok
from app.core.config import settings
from app.models import Document
from app.retrieval.fusion import rrf_fuse
from app.retrieval.service import keyword_results_for
from app.retrieval.vector_search import vector_search

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


class SearchRequest(BaseModel):
    """检索调试请求体（双路召回 + RRF 融合）。"""

    query: str = Field(min_length=1, max_length=1000)
    kb_ids: list[str] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=10, ge=1, le=50)


def _attach_document_names(db, items: list[dict]) -> list[dict]:
    """按 metadata.document_id 批量补充 document_name（filename）。"""
    doc_ids = {
        item["metadata"].get("document_id")
        for item in items
        if item["metadata"].get("document_id")
    }
    if not doc_ids:
        return items
    rows = db.execute(
        Document.__table__.select().where(Document.id.in_(doc_ids))
    ).all()
    names = {str(row.id): row.filename for row in rows}
    for item in items:
        item["document_name"] = names.get(item["metadata"].get("document_id"))
    return items


@router.post("/search")
def search(body: SearchRequest, db: DBSession, user: CurrentUser) -> dict:
    """文档 chunk 混合检索：BM25 + 向量双路召回，RRF 融合排序返回。

    召回数取 .env 配置（VECTOR_TOP_K / KEYWORD_TOP_K）与请求 top_k 的较大值，
    融合后截断到请求的 top_k。
    """
    visible = get_visible_kb_ids(db, user)
    target_ids = [kb_id for kb_id in body.kb_ids if kb_id in visible]
    if not body.query.strip() or not target_ids:
        return ok({"mode": "hybrid_rrf", "top_k": body.top_k, "items": []})

    vector_results = vector_search(
        body.query, target_ids, top_k=max(settings.VECTOR_TOP_K, body.top_k)
    )
    keyword_results = keyword_results_for(
        db, body.query, target_ids, max(settings.KEYWORD_TOP_K, body.top_k)
    )
    items = rrf_fuse(vector_results, keyword_results, top_n=body.top_k)

    items = _attach_document_names(db, items)
    return ok({"mode": "hybrid_rrf", "top_k": body.top_k, "items": items})
