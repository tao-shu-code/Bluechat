"""Rerank（Task 8）：OpenAI 兼容 Rerank API 自定义客户端（httpx 同步）。

- 端点自适应：base 以 /rerank 结尾原样使用；以 /v1 结尾拼 /rerank；否则拼 /v1/rerank
  （兼容 Jina https://api.jina.ai/v1、自建服务 http://host:port 等）；
- body：{model, query, documents, top_n}；响应解析 results:[{index, relevance_score}]
  （Jina/Cohere 风格，兼容 score 字段名）；
- 任何失败（未启用/超时/非 200/解析失败）均记日志并降级返回原顺序候选（不抛异常）。
"""

from urllib.parse import urlparse

import httpx
from langsmith import traceable

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger(__name__)


def resolve_rerank_url(base: str) -> str:
    """由 RERANK_API_BASE 推导 rerank 端点（/rerank 或 /v1/rerank 自适应）。"""
    base = base.strip().rstrip("/")
    path = urlparse(base).path.rstrip("/")
    if path.endswith("/rerank"):
        return base
    if path.endswith("/v1"):
        return f"{base}/rerank"
    return f"{base}/v1/rerank"


def parse_relevance_scores(payload: dict) -> list[tuple[int, float]]:
    """解析 rerank 响应为 [(候选下标, 相关性得分)]，忽略非法项。"""
    results = payload.get("results") if isinstance(payload, dict) else None
    pairs: list[tuple[int, float]] = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        score = item.get("relevance_score", item.get("score"))
        if isinstance(index, int) and isinstance(score, (int, float)):
            pairs.append((index, float(score)))
    return pairs


@traceable(name="retrieval.rerank", run_type="chain")
def rerank(query: str, candidates: list[dict], *, top_n: int | None = None) -> list[dict]:
    """按 query 相关性重排 candidates（元素为 fusion 输出结构），返回顺序调整后的列表。

    - 成功：新 dict 列表，score 与 rerank_score 均为相关性得分，其余字段（含两路原始分）保留；
    - 未启用 / 失败 / 超时 / 非 200 / 解析为空：warning 日志并降级返回原顺序（截断到 top_n）；
    - top_n 默认为候选总数，两种路径均保证返回长度 ≤ top_n。
    """
    limit = len(candidates) if top_n is None else max(min(top_n, len(candidates)), 0)
    if limit == 0:
        return []
    if not settings.RERANK_ENABLED:
        return candidates[:limit]

    body = {
        "model": settings.RERANK_MODEL,
        "query": query,
        "documents": [str(item.get("content", "")) for item in candidates],
        "top_n": limit,
    }
    headers = {"Content-Type": "application/json"}
    if settings.RERANK_API_KEY:
        headers["Authorization"] = f"Bearer {settings.RERANK_API_KEY}"
    url = resolve_rerank_url(settings.RERANK_API_BASE)

    try:
        with httpx.Client(timeout=settings.RERANK_TIMEOUT) as client:
            response = client.post(url, json=body, headers=headers)
        if response.status_code != 200:
            logger.warning(
                "rerank api non-200 status=%s url=%s, degrade to fusion order",
                response.status_code,
                url,
            )
            return candidates[:limit]
        pairs = parse_relevance_scores(response.json())
    except Exception as exc:
        logger.warning("rerank failed, degrade to fusion order: %s", exc)
        return candidates[:limit]

    if not pairs:
        logger.warning("rerank api returned no usable results, degrade to fusion order")
        return candidates[:limit]

    ranked: list[dict] = []
    for index, relevance in pairs:
        if not 0 <= index < len(candidates):
            continue
        item = dict(candidates[index])
        item["rerank_score"] = relevance
        item["score"] = relevance
        ranked.append(item)
    if not ranked:
        logger.warning("rerank api returned invalid indices, degrade to fusion order")
        return candidates[:limit]
    return ranked[:limit]
