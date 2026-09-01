"""RRF 融合（Task 7）：纯函数、无 I/O，可离线单测（python -m app.retrieval.fusion 自检）。

score = Σ 1/(k + rank_i)，k=60；两路有序结果按 chunk 内容 + document_id 去重合并，
融合项保留两路原始 score 与排名（vector_score / keyword_score 等）供调试日志。
"""

RRF_K = 60


def dedup_key(hit: dict) -> tuple:
    """去重键：chunk 内容 + document_id（metadata 缺失时 document_id 记 None）。"""
    metadata = hit.get("metadata") or {}
    return (hit.get("content", ""), metadata.get("document_id"))


def rrf_fuse(
    vector_results: list[dict],
    keyword_results: list[dict],
    *,
    k: int = RRF_K,
    top_n: int | None = None,
) -> list[dict]:
    """两路有序结果做 Reciprocal Rank Fusion，返回融合排序后的新列表。

    - rank 从 1 开始：score = Σ 1/(k + rank_i)，两路都命中的 chunk 权重叠加；
    - 去重：相同 (content, document_id) 只保留一条，metadata 取首次出现的（排名更高的路）；
    - 排序：score 降序（stable，同分保持首次出现顺序）；
    - top_n 给定时截断；任一路结果为空时等价于单路结果重排。
    """
    fused: dict[tuple, dict] = {}
    for name, results in (("vector", vector_results), ("keyword", keyword_results)):
        for rank, hit in enumerate(results, start=1):
            key = dedup_key(hit)
            entry = fused.get(key)
            if entry is None:
                entry = {
                    "content": hit.get("content", ""),
                    "metadata": dict(hit.get("metadata") or {}),
                    "score": 0.0,
                    "vector_score": None,
                    "keyword_score": None,
                    "vector_rank": None,
                    "keyword_rank": None,
                }
                fused[key] = entry
            entry["score"] += 1.0 / (k + rank)
            entry[f"{name}_score"] = hit.get("score")
            entry[f"{name}_rank"] = rank

    ordered = sorted(fused.values(), key=lambda item: item["score"], reverse=True)
    if top_n is not None:
        ordered = ordered[:top_n]
    return ordered


if __name__ == "__main__":
    # 离线自检：RRF 融合 / 去重 / top_n
    vector_hits = [
        {"content": "alpha", "metadata": {"document_id": "d1"}, "score": 0.9},
        {"content": "beta", "metadata": {"document_id": "d2"}, "score": 0.8},
    ]
    keyword_hits = [
        {"content": "beta", "metadata": {"document_id": "d2"}, "score": 0.5},
        {"content": "gamma", "metadata": {"document_id": "d3"}, "score": 0.3},
    ]
    fused = rrf_fuse(vector_hits, keyword_hits)
    assert [item["content"] for item in fused] == ["beta", "alpha", "gamma"], fused
    assert abs(fused[0]["score"] - (1 / (RRF_K + 2) + 1 / (RRF_K + 1))) < 1e-9
    assert fused[0]["vector_score"] == 0.8 and fused[0]["keyword_score"] == 0.5
    assert len(rrf_fuse(vector_hits, keyword_hits, top_n=1)) == 1
    assert rrf_fuse([], [], top_n=3) == []
    print("fusion self-check passed:", [(item["content"], round(item["score"], 6)) for item in fused])
