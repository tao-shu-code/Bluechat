"""LLM 封装（Task 9.1）：LangChain ChatOpenAI 的流式 / 一次性文本生成。

- base_url / api_key / model 取自 settings（LLM_API_BASE / LLM_API_KEY / LLM_MODEL），
  temperature 固定 0.3；LLM_API_BASE 留空时回退 OpenAI 官方默认地址；
- stream_answer(messages)：流式调用（OpenAI SDK 直连），逐段 yield 文本增量，
  并把供应商返回的真实 token 用量写入当前 LangSmith 运行节点；
- complete(messages)：一次性调用，返回完整回答文本；
- LLM_API_KEY 未配置时调用直接抛 BizError(5001, "LLM 服务未配置")，
  由全局异常处理器统一转换为响应结构。

Token 统计说明：部分兼容服务（如 SiliconFlow）流式响应的每个 chunk 都携带
累计 usage，LangChain 会对各 chunk 的 usage 求和导致 LangSmith 统计虚高。
因此流式改用 OpenAI SDK 直连（stream_options.include_usage），只取最后一个
chunk 的累计 usage（即真实最终值），并通过 RunTree.set 上报 LangSmith。
"""

from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langsmith import get_current_run_tree, traceable
from openai import OpenAI

from app.common.exceptions import BizError
from app.core.config import settings
from app.core.log import get_logger

logger = get_logger(__name__)

# BizError 业务码约定（qa 模块）：5001 LLM 服务未配置
CODE_LLM_NOT_CONFIGURED = 5001

TEMPERATURE = 0.3

_ROLE_MAP = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}


def _ensure_configured() -> None:
    """LLM_API_KEY 未配置时快速失败。"""
    if not settings.LLM_API_KEY:
        raise BizError(CODE_LLM_NOT_CONFIGURED, "LLM 服务未配置")


def _build_llm(*, streaming: bool) -> ChatOpenAI:
    """构造 ChatOpenAI 实例（非流式路径使用，usage 由服务端一次性返回，统计准确）。"""
    return ChatOpenAI(
        base_url=settings.LLM_API_BASE or None,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL,
        streaming=streaming,
        temperature=TEMPERATURE,
    )


def _content_to_text(content: Any) -> str:
    """消息 content 归一化为纯文本（兼容 str 与多模态 parts 列表两种形态）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


def _messages_to_dicts(messages: Sequence[BaseMessage]) -> list[dict]:
    """LangChain 消息 → OpenAI 格式 [{"role", "content"}]。"""
    out: list[dict] = []
    for m in messages:
        role = _ROLE_MAP.get(getattr(m, "type", ""), "user")
        out.append({"role": role, "content": _content_to_text(getattr(m, "content", m))})
    return out


def _usage_dict(usage) -> dict | None:
    """OpenAI usage 对象 → 精简 dict（供应商未返回用量时为 None）。"""
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens or 0,
        "completion_tokens": usage.completion_tokens or 0,
        "total_tokens": usage.total_tokens or 0,
    }


@traceable(name="ChatOpenAI", run_type="llm")
def stream_answer(messages: Sequence[BaseMessage], usage_sink: dict | None = None) -> Iterator[str]:
    """流式生成回答（OpenAI SDK 直连）：逐段 yield 文本增量。

    结束时把供应商返回的真实 token 用量（最后一个 chunk 的累计 usage）
    写入当前 LangSmith 运行节点；传入 usage_sink 时同步写入该 dict
    （键 prompt_tokens/completion_tokens/total_tokens），供调用方落库。
    """
    _ensure_configured()
    client = OpenAI(
        base_url=settings.LLM_API_BASE or None, api_key=settings.LLM_API_KEY
    )
    usage = None
    stream = client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=_messages_to_dicts(messages),
        stream=True,
        stream_options={"include_usage": True},
        temperature=TEMPERATURE,
    )
    try:
        for chunk in stream:
            # 部分 provider 每个 chunk 都带累计 usage：只保留最后一个（即最终值）
            if getattr(chunk, "usage", None) is not None:
                usage = chunk.usage
            if chunk.choices:
                piece = getattr(chunk.choices[0].delta, "content", None)
                if piece:
                    yield piece
    finally:
        final_usage = _usage_dict(usage)
        if final_usage is not None:
            if usage_sink is not None:
                usage_sink.update(final_usage)
            rt = get_current_run_tree()
            if rt is not None:
                try:
                    rt.set(
                        usage_metadata={
                            "input_tokens": final_usage["prompt_tokens"],
                            "output_tokens": final_usage["completion_tokens"],
                            "total_tokens": final_usage["total_tokens"],
                        }
                    )
                except Exception as exc:
                    logger.warning("report usage to langsmith failed: %s", exc)


def complete(
    messages: Sequence[BaseMessage], model: str | None = None
) -> tuple[str, dict | None]:
    """一次性生成完整回答，返回 (回答文本, token 用量 dict | None)。

    model 缺省用 LLM_MODEL；usage 取自 AIMessage.usage_metadata
    （LangChain 从服务端 usage 字段填充）。
    """
    _ensure_configured()
    llm = _build_llm(streaming=False, model=model)
    response = llm.invoke(messages)
    meta = getattr(response, "usage_metadata", None) or {}
    usage = None
    if meta.get("total_tokens") is not None:
        usage = {
            "prompt_tokens": meta.get("input_tokens") or 0,
            "completion_tokens": meta.get("output_tokens") or 0,
            "total_tokens": meta.get("total_tokens") or 0,
        }
    return _content_to_text(response.content), usage
