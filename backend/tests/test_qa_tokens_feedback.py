"""问答 Token 统计与回答反馈逻辑单元测试（不依赖真实中间件）。

- stream_answer 的 usage_sink 填充（mock OpenAI client）；
- complete 返回 (text, usage)（mock LLM）；
- save_message 将 usage 写入 Message 字段（stub db session）；
- _message_payload 的 tokens/feedback 输出；
- FeedbackRequest 取值校验。
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

import app.qa.llm as llm_module
import app.session.router as session_router
from app.models import Message
from app.models.enums import MessageRole
from app.qa.llm import complete, stream_answer
from app.qa.router import FeedbackRequest
from app.session.router import _message_payload, save_message


# ---------- stream_answer usage_sink ----------

class _FakeChoice:
    def __init__(self, content):
        self.delta = SimpleNamespace(content=content)


class _FakeChunk:
    def __init__(self, content=None, usage=None):
        self.usage = usage
        self.choices = [_FakeChoice(content)] if content is not None else []


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    def create(self, **kwargs):
        return iter(self._chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks))


def test_stream_answer_fills_usage_sink(monkeypatch):
    chunks = [
        _FakeChunk(content="你好"),
        _FakeChunk(content="！"),
        _FakeChunk(usage=_FakeUsage()),  # 最后一个 chunk 携带累计 usage
    ]
    monkeypatch.setattr(llm_module, "OpenAI", lambda **kw: _FakeClient(chunks))
    sink = {}
    pieces = list(stream_answer([object()], usage_sink=sink))
    assert pieces == ["你好", "！"]
    assert sink == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_stream_answer_without_sink_still_works(monkeypatch):
    monkeypatch.setattr(llm_module, "OpenAI", lambda **kw: _FakeClient([_FakeChunk(usage=_FakeUsage())]))
    assert list(stream_answer([object()])) == []


def test_stream_answer_no_usage_keeps_sink_empty(monkeypatch):
    # 供应商未返回 usage：sink 保持空 dict（拒答/异常路径不落 token 列）
    monkeypatch.setattr(llm_module, "OpenAI", lambda **kw: _FakeClient([_FakeChunk(content="hi")]))
    sink = {}
    list(stream_answer([object()], usage_sink=sink))
    assert sink == {}


# ---------- complete 返回 usage ----------

class _FakeResponse:
    content = "回答文本"
    usage_metadata = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}


def test_complete_returns_text_and_usage(monkeypatch):
    monkeypatch.setattr(
        llm_module,
        "_build_llm",
        lambda streaming, model=None: SimpleNamespace(invoke=lambda messages: _FakeResponse()),
    )
    monkeypatch.setattr(llm_module.settings, "LLM_API_KEY", "sk-test")
    text, usage = complete([object()])
    assert text == "回答文本"
    assert usage == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def test_complete_without_usage_metadata(monkeypatch):
    resp = SimpleNamespace(content="答", usage_metadata=None)
    monkeypatch.setattr(
        llm_module,
        "_build_llm",
        lambda streaming, model=None: SimpleNamespace(invoke=lambda m: resp),
    )
    monkeypatch.setattr(llm_module.settings, "LLM_API_KEY", "sk-test")
    text, usage = complete([object()])
    assert text == "答"
    assert usage is None


# ---------- save_message 落 usage ----------

class _FakeDb:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def execute(self, *args, **kwargs):
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def test_save_message_persists_usage(monkeypatch):
    monkeypatch.setattr(session_router.redis_cache, "append_message", lambda *a, **kw: None)
    db = _FakeDb()
    msg = save_message(
        db,
        str(uuid4()),
        "assistant",
        "回答",
        usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    )
    assert msg.prompt_tokens == 1
    assert msg.completion_tokens == 2
    assert msg.total_tokens == 3


def test_save_message_without_usage_keeps_null(monkeypatch):
    monkeypatch.setattr(session_router.redis_cache, "append_message", lambda *a, **kw: None)
    msg = save_message(_FakeDb(), str(uuid4()), "user", "问")
    assert msg.prompt_tokens is None
    assert msg.total_tokens is None


# ---------- _message_payload ----------

def test_message_payload_tokens_and_feedback():
    msg = Message(
        conversation_id=uuid4(),
        role=MessageRole.assistant,
        content="x",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        feedback="like",
    )
    payload = _message_payload(msg)
    assert payload["tokens"] == {
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 3,
    }
    assert payload["feedback"] == "like"


def test_message_payload_null_tokens():
    msg = Message(conversation_id=uuid4(), role=MessageRole.user, content="q")
    payload = _message_payload(msg)
    assert payload["tokens"] is None
    assert payload["feedback"] is None


# ---------- FeedbackRequest 校验 ----------

def test_feedback_request_accepts_valid_values():
    assert FeedbackRequest(feedback="like").feedback == "like"
    assert FeedbackRequest(feedback="dislike").feedback == "dislike"
    assert FeedbackRequest(feedback=None).feedback is None


def test_feedback_request_rejects_invalid_value():
    with pytest.raises(ValidationError):
        FeedbackRequest(feedback="bad")
