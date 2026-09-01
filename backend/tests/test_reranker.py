"""Reranker 单元测试（Task 8）：降级路径（未启用/超时/非200/空结果）+ 成功重排（stub httpx）。"""

from types import SimpleNamespace

import httpx
import pytest

import app.retrieval.reranker as reranker
from app.core.config import settings
from app.retrieval.reranker import parse_relevance_scores, resolve_rerank_url, rerank

CANDIDATES = [
    {"content": "文档甲", "score": 0.1, "metadata": {"document_id": "d1"}},
    {"content": "文档乙", "score": 0.2, "metadata": {"document_id": "d2"}},
    {"content": "文档丙", "score": 0.3, "metadata": {"document_id": "d3"}},
]


class StubResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class StubClient:
    """替换 reranker 模块引用的 httpx.Client（支持 with 上下文）。"""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.captured: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json=None, headers=None):
        self.captured = {"url": url, "json": json, "headers": headers}
        if self.exc is not None:
            raise self.exc
        return self.response


def _patch_httpx(monkeypatch, response=None, exc=None) -> StubClient:
    client = StubClient(response=response, exc=exc)
    monkeypatch.setattr(reranker, "httpx", SimpleNamespace(Client=lambda timeout=None: client))
    return client


@pytest.fixture()
def rerank_enabled(monkeypatch):
    monkeypatch.setattr(settings, "RERANK_ENABLED", True)
    monkeypatch.setattr(settings, "RERANK_API_BASE", "https://rerank.example.com")
    monkeypatch.setattr(settings, "RERANK_API_KEY", "test-key")


class TestDegradePaths:
    def test_disabled_returns_original_order(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_ENABLED", False)
        client = _patch_httpx(monkeypatch, response=StubResponse(200))
        result = rerank("q", CANDIDATES)
        assert [item["content"] for item in result] == ["文档甲", "文档乙", "文档丙"]
        assert "rerank_score" not in result[0]
        assert client.captured == {}  # 未启用时不得发起 HTTP 请求

    def test_timeout_degrades_to_original_order(self, rerank_enabled, monkeypatch):
        client = _patch_httpx(monkeypatch, exc=httpx.TimeoutException("connect timeout"))
        result = rerank("q", CANDIDATES)  # 不应抛异常
        assert [item["content"] for item in result] == ["文档甲", "文档乙", "文档丙"]
        assert "rerank_score" not in result[0]

    def test_non_200_degrades_to_original_order(self, rerank_enabled, monkeypatch):
        _patch_httpx(monkeypatch, response=StubResponse(status_code=503))
        result = rerank("q", CANDIDATES)
        assert [item["content"] for item in result] == ["文档甲", "文档乙", "文档丙"]
        assert result[0]["score"] == 0.1  # 原始 score 保留

    def test_empty_results_degrades(self, rerank_enabled, monkeypatch):
        _patch_httpx(monkeypatch, response=StubResponse(200, {"results": []}))
        assert [i["content"] for i in rerank("q", CANDIDATES)] == ["文档甲", "文档乙", "文档丙"]

    def test_invalid_payload_degrades(self, rerank_enabled, monkeypatch):
        _patch_httpx(monkeypatch, response=StubResponse(200, {"unexpected": True}))
        assert [i["content"] for i in rerank("q", CANDIDATES)] == ["文档甲", "文档乙", "文档丙"]

    def test_degrade_respects_top_n(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_ENABLED", False)
        result = rerank("q", CANDIDATES, top_n=2)
        assert [item["content"] for item in result] == ["文档甲", "文档乙"]


class TestSuccessfulRerank:
    def test_reorder_and_scores(self, rerank_enabled, monkeypatch):
        payload = {
            "results": [
                {"index": 2, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.60},
                {"index": 1, "relevance_score": 0.10},
            ]
        }
        client = _patch_httpx(monkeypatch, response=StubResponse(200, payload))
        result = rerank("q", CANDIDATES)
        assert [item["content"] for item in result] == ["文档丙", "文档甲", "文档乙"]
        assert result[0]["rerank_score"] == 0.95
        assert result[0]["score"] == 0.95
        # 其余字段（含 metadata）保留
        assert result[0]["metadata"] == {"document_id": "d3"}
        # 请求体与端点
        assert client.captured["json"]["query"] == "q"
        assert client.captured["json"]["documents"] == ["文档甲", "文档乙", "文档丙"]
        assert client.captured["json"]["top_n"] == 3
        assert client.captured["url"].endswith("/v1/rerank")
        assert client.captured["headers"]["Authorization"] == "Bearer test-key"

    def test_top_n_truncation_on_success(self, rerank_enabled, monkeypatch):
        payload = {
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.1},
                {"index": 2, "relevance_score": 0.2},
            ]
        }
        _patch_httpx(monkeypatch, response=StubResponse(200, payload))
        result = rerank("q", CANDIDATES, top_n=2)
        assert [item["content"] for item in result] == ["文档乙", "文档甲"]

    def test_out_of_range_index_skipped(self, rerank_enabled, monkeypatch):
        payload = {
            "results": [
                {"index": 99, "relevance_score": 1.0},
                {"index": 1, "relevance_score": 0.5},
            ]
        }
        _patch_httpx(monkeypatch, response=StubResponse(200, payload))
        result = rerank("q", CANDIDATES)
        assert [item["content"] for item in result] == ["文档乙"]

    def test_empty_candidates(self, rerank_enabled, monkeypatch):
        client = _patch_httpx(monkeypatch, response=StubResponse(200))
        assert rerank("q", []) == []
        assert client.captured == {}


class TestHelpers:
    @pytest.mark.parametrize(
        ("base", "expected"),
        [
            ("https://api.example.com/rerank", "https://api.example.com/rerank"),
            ("https://api.jina.ai/v1/", "https://api.jina.ai/v1/rerank"),
            ("http://host:9090", "http://host:9090/v1/rerank"),
        ],
    )
    def test_resolve_rerank_url(self, base, expected):
        assert resolve_rerank_url(base) == expected

    def test_parse_relevance_scores(self):
        payload = {
            "results": [
                {"index": 1, "relevance_score": 0.7},
                {"index": "bad", "relevance_score": 0.9},  # 非法 index 忽略
                {"index": 2, "score": 0.4},  # 兼容 score 字段名
                "not-a-dict",
                {"index": 3},  # 缺少得分忽略
            ]
        }
        assert parse_relevance_scores(payload) == [(1, 0.7), (2, 0.4)]
        assert parse_relevance_scores(None) == []
        assert parse_relevance_scores({"results": None}) == []
