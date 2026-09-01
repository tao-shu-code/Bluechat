"""RRF 融合单元测试（Task 7）：分数数学断言 / 跨路去重 / top_n / 空输入。"""

from app.retrieval.fusion import RRF_K, dedup_key, rrf_fuse


def _hit(content: str, document_id: str, score: float) -> dict:
    return {"content": content, "metadata": {"document_id": document_id}, "score": score}


class TestRrfScore:
    def test_two_path_score_math(self):
        vector = [_hit("alpha", "d1", 0.9), _hit("beta", "d2", 0.8)]
        keyword = [_hit("beta", "d2", 0.5), _hit("gamma", "d3", 0.3)]
        fused = rrf_fuse(vector, keyword)
        # beta 两路命中：vector rank2 + keyword rank1 → 分数最高
        assert [item["content"] for item in fused] == ["beta", "alpha", "gamma"]
        assert abs(fused[0]["score"] - (1 / (RRF_K + 2) + 1 / (RRF_K + 1))) < 1e-12
        assert abs(fused[1]["score"] - 1 / (RRF_K + 1)) < 1e-12
        assert abs(fused[2]["score"] - 1 / (RRF_K + 2)) < 1e-12

    def test_original_scores_and_ranks_preserved(self):
        vector = [_hit("alpha", "d1", 0.9), _hit("beta", "d2", 0.8)]
        keyword = [_hit("beta", "d2", 0.5)]
        fused = rrf_fuse(vector, keyword)
        beta = fused[0]
        assert beta["vector_score"] == 0.8 and beta["keyword_score"] == 0.5
        assert beta["vector_rank"] == 2 and beta["keyword_rank"] == 1
        alpha = fused[1]
        assert alpha["vector_score"] == 0.9 and alpha["keyword_score"] is None
        assert alpha["vector_rank"] == 1 and alpha["keyword_rank"] is None

    def test_custom_k(self):
        fused = rrf_fuse([_hit("a", "d1", 1.0)], [], k=10)
        assert abs(fused[0]["score"] - 1 / 11) < 1e-12


class TestDedup:
    def test_same_content_same_doc_merged(self):
        vector = [_hit("same", "d1", 0.9)]
        keyword = [_hit("same", "d1", 0.4)]
        fused = rrf_fuse(vector, keyword)
        assert len(fused) == 1
        # 两路分数叠加：各贡献 1/(k+1)
        assert abs(fused[0]["score"] - 2 / (RRF_K + 1)) < 1e-12
        assert fused[0]["vector_score"] == 0.9
        assert fused[0]["keyword_score"] == 0.4

    def test_same_content_different_doc_not_merged(self):
        fused = rrf_fuse([_hit("same", "d1", 0.9)], [_hit("same", "d2", 0.4)])
        assert len(fused) == 2

    def test_metadata_from_first_occurrence(self):
        vector = [_hit("same", "d1", 0.9)]
        keyword = [{"content": "same", "metadata": {"document_id": "d1", "extra": 1}, "score": 0.4}]
        fused = rrf_fuse(vector, keyword)
        # metadata 取首次出现（排名更高的 vector 路）
        assert fused[0]["metadata"] == {"document_id": "d1"}

    def test_metadata_copied_not_referenced(self):
        meta = {"document_id": "d1"}
        fused = rrf_fuse([{"content": "a", "metadata": meta, "score": 1.0}], [])
        fused[0]["metadata"]["document_id"] = "mutated"
        assert meta["document_id"] == "d1"

    def test_dedup_key_missing_metadata(self):
        assert dedup_key({"content": "x"}) == ("x", None)
        assert dedup_key({"content": "x", "metadata": None}) == ("x", None)


class TestTopNAndEmpty:
    def test_top_n_truncates(self):
        vector = [_hit(f"c{i}", f"d{i}", 0.9 - i * 0.1) for i in range(5)]
        fused = rrf_fuse(vector, [], top_n=2)
        assert [item["content"] for item in fused] == ["c0", "c1"]

    def test_top_n_larger_than_results(self):
        vector = [_hit("a", "d1", 0.9)]
        assert len(rrf_fuse(vector, [], top_n=10)) == 1

    def test_empty_inputs(self):
        assert rrf_fuse([], []) == []
        assert rrf_fuse([], [], top_n=3) == []

    def test_single_side_empty_equals_reorder(self):
        fused = rrf_fuse([_hit("a", "d1", 0.9)], [])
        assert [item["content"] for item in fused] == ["a"]
        assert abs(fused[0]["score"] - 1 / (RRF_K + 1)) < 1e-12
