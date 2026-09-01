"""BM25 模块单元测试（不依赖真实 PostgreSQL，全部 stub/mock）。"""

import uuid

import pytest

from app.retrieval import bm25


@pytest.fixture(autouse=True)
def reset_state():
    """每个用例前重置模块级状态缓存。"""
    bm25._state = {"ensured": False, "available": False, "tokenizer": None}
    yield
    bm25._state = {"ensured": False, "available": False, "tokenizer": None}


class FakeDb:
    """最小 DB stub：execute 返回预设结果，commit/rollback 均成功。"""

    def __init__(self, execute_error=None, rows=None):
        self.execute_error = execute_error
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(str(sql))
        if self.execute_error:
            raise self.execute_error
        return self

    def all(self):
        return self.rows

    def commit(self):
        pass

    def rollback(self):
        pass


def test_empty_query_returns_empty_list():
    assert bm25.bm25_search(None, "", ["kb1"]) == []
    assert bm25.bm25_search(None, "   ", ["kb1"]) == []


def test_empty_kb_ids_returns_empty_list():
    assert bm25.bm25_search(None, "年假", []) == []


def test_not_ensured_returns_none():
    # 未执行 ensure_bm25_index 时（available=False）返回 None 供调用方回退
    assert bm25.bm25_search(None, "年假", ["kb1"]) is None


def test_ensure_success_first_tokenizer():
    db = FakeDb()
    assert bm25.ensure_bm25_index(db) is True
    assert bm25.bm25_available() is True
    assert bm25._state["tokenizer"] == "chinese_lindera"
    # 索引 SQL 包含 id 列（pg_search 要求 key_field 在索引列中）
    assert any("USING bm25 (id, content)" in s for s in db.executed)
    assert any("chinese_lindera" in s for s in db.executed)


def test_ensure_falls_back_to_ngram():
    # chinese_lindera 失败（第一次 execute 抛异常），ngram 成功
    calls = {"n": 0}

    class FlakyDb(FakeDb):
        def execute(self, sql, params=None):
            self.executed.append(str(sql))
            if "chinese_lindera" in str(sql):
                calls["n"] += 1
                raise RuntimeError("tokenizer unavailable")
            return self

    db = FlakyDb()
    assert bm25.ensure_bm25_index(db) is True
    assert bm25._state["tokenizer"] == "ngram"
    assert any("min_gram" in s for s in db.executed)


def test_ensure_all_failed_returns_false():
    db = FakeDb(execute_error=RuntimeError("no extension"))
    assert bm25.ensure_bm25_index(db) is False
    assert bm25.bm25_available() is False


def test_ensure_is_idempotent():
    db = FakeDb()
    assert bm25.ensure_bm25_index(db) is True
    first_count = len(db.executed)
    # 第二次调用命中进程内缓存，不再执行 SQL
    assert bm25.ensure_bm25_index(db) is True
    assert len(db.executed) == first_count


def test_bm25_search_maps_rows():
    doc_id, kb_id = uuid.uuid4(), uuid.uuid4()
    rows = [
        ("员工年假有五天", "假期 > 年假", 1, 0, doc_id, kb_id, 1.9061548),
        ("报销需要发票", "财务 > 报销", 2, 3, doc_id, kb_id, 0.8),
    ]
    bm25._state.update({"ensured": True, "available": True, "tokenizer": "chinese_lindera"})
    db = FakeDb(rows=rows)
    results = bm25.bm25_search(db, "年假", ["kb1"], top_k=5)
    assert len(results) == 2
    assert results[0]["content"] == "员工年假有五天"
    assert results[0]["metadata"] == {
        "document_id": str(doc_id),
        "kb_id": str(kb_id),
        "title_path": "假期 > 年假",
        "page_number": 1,
        "chunk_index": 0,
    }
    assert results[0]["score"] == pytest.approx(1.9061548)
    assert results[1]["score"] == pytest.approx(0.8)


def test_bm25_search_query_error_returns_none():
    bm25._state.update({"ensured": True, "available": True, "tokenizer": "ngram"})
    db = FakeDb(execute_error=RuntimeError("boom"))
    assert bm25.bm25_search(db, "年假", ["kb1"]) is None
