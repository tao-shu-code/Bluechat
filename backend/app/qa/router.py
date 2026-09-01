"""LLM 问答接口（Task 9.3）：POST /api/qa/chat，默认 SSE 流式，兼容 stream=false 一次性 JSON。

SSE 事件协议（每条为 "event: {event}\\ndata: {json}\\n\\n"，json 均为 UTF-8、ensure_ascii=False）：
1. event: sources → data: 引用来源列表 JSON（[{document_name, title_path, page_number}]），
   检索完成后立即发送；
2. event: delta   → data: {"content": "..."}，LLM 文本增量，逐段发送；
3. event: done    → data: {"conversation_id": "...", "message_id": "..."}，流正常结束；
4. event: error   → data: {"message": "..."}，生成失败（此后不再有其他事件）。

流结束后保存 user 消息与 assistant 完整回答（sources jsonb），输出检索耗时 /
首 token 耗时 / 总耗时打点日志，并写审计日志 action=ask。
限流：Redis 滑动窗口（qa:rate:{user_id}），超限返回 429；Redis 故障降级放行。
"""

import json
import time
import uuid as uuid_module

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langsmith import trace, traceable
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.audit import audit_log
from app.common.deps import CurrentUser, DBSession, get_visible_kb_ids
from app.common.exceptions import BizError
from app.common.response import ok
from app.core.config import settings
from app.core.log import get_logger
from app.models import Conversation, Message, MessageRole, User
from app.qa.llm import complete, stream_answer
from app.qa.rate_limit import check_rate_limit
from app.qa.service import NO_ANSWER_TEXT, prepare_chat
from app.session.router import _get_owned_conversation, save_message

logger = get_logger(__name__)

router = APIRouter(prefix="/api/qa", tags=["qa"])

DEFAULT_CONVERSATION_TITLE = "新会话"
# 生成失败时对外的通用文案（业务异常如"LLM 服务未配置"原样透出）
GENERIC_STREAM_ERROR = "生成回答失败，请稍后重试"


class ChatRequest(BaseModel):
    """提问请求体。"""

    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    kb_ids: list[str] | None = None
    stream: bool = True


def _client_ip(request: Request) -> str | None:
    """客户端 IP（无连接信息时为 None）。"""
    return request.client.host if request.client else None


def _sse(event: str, data) -> str:
    """单条 SSE 事件帧。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _log_timings(
    conversation_id: str,
    retrieval_ms: float,
    first_token_ms: float | None,
    total_ms: float,
    no_answer: bool,
    answer_len: int,
) -> None:
    """问答耗时打点日志（检索耗时 / 首 token 耗时 / 总耗时）。"""
    logger.info(
        "qa timings conversation=%s retrieval=%.1fms first_token=%.1fms total=%.1fms "
        "no_answer=%s answer_len=%s",
        conversation_id,
        retrieval_ms,
        first_token_ms if first_token_ms is not None else -1.0,
        total_ms,
        no_answer,
        answer_len,
    )


def _audit_ask(
    db: Session,
    user: User,
    ip: str | None,
    conversation_id: str,
    question: str,
    kb_ids: list[str] | None,
    no_answer: bool,
) -> None:
    """写提问审计日志（action=ask）。"""
    audit_log(
        db,
        user.id,
        "ask",
        detail={
            "question": question[:500],
            "conversation_id": conversation_id,
            "kb_ids": kb_ids,
            "no_answer": no_answer,
        },
        ip=ip,
    )


@traceable(name="qa.chat_stream", run_type="chain", hide_inputs=["db", "user"])
def _event_stream(
    db: Session,
    user: User,
    ip: str | None,
    question: str,
    conversation_id: str,
    kb_ids: list[str] | None,
    started: float,
):
    """SSE 事件流（单一 LangSmith Trace 根）：prepare_chat → sources → delta* → done。

    prepare_chat 在本生成器内执行，使其与 LLM 生成同属一个追踪上下文
    （qa.prepare_chat / retrieval.* / ChatOpenAI 均作为子节点嵌套）。
    异常发 error；结束时保存消息并打点审计。
    """
    prepared = prepare_chat(
        db,
        user,
        question,
        conversation_id=conversation_id,
        kb_ids=kb_ids,
    )
    retrieval_ms = prepared["retrieval_ms"]
    first_token_ms: float | None = None
    full_answer = ""
    usage: dict = {}
    saved = False

    def _persist() -> str | None:
        """保存 user 消息与 assistant 回答（sources jsonb + token 用量），返回消息 ID。"""
        save_message(db, conversation_id, "user", question)
        msg = None
        if full_answer:
            msg = save_message(
                db,
                conversation_id,
                "assistant",
                full_answer,
                sources=prepared["sources"],
                usage=usage or None,
            )
        return str(msg.id) if msg else None

    try:
        yield _sse("sources", prepared["sources"])
        if prepared["no_answer"]:
            first_token_ms = (time.perf_counter() - started) * 1000
            full_answer = NO_ANSWER_TEXT
            yield _sse("delta", {"content": full_answer})
        else:
            for piece in stream_answer(prepared["messages"], usage_sink=usage):
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000
                full_answer += piece
                yield _sse("delta", {"content": piece})
        message_id = _persist()
        saved = True
        yield _sse(
            "done",
            {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "usage": usage or None,
            },
        )
    except Exception as exc:
        logger.error("qa stream failed conversation=%s: %s", conversation_id, exc)
        message = exc.message if isinstance(exc, BizError) else GENERIC_STREAM_ERROR
        yield _sse("error", {"message": message})
    finally:
        if not saved:
            # 异常 / 客户端断开路径：尽力保存 user 消息与已生成内容（静默，不抛错）
            try:
                _persist()
            except Exception as exc:
                logger.warning(
                    "qa persist on abort failed conversation=%s: %s", conversation_id, exc
                )
        total_ms = (time.perf_counter() - started) * 1000
        _log_timings(
            conversation_id,
            retrieval_ms,
            first_token_ms,
            total_ms,
            prepared["no_answer"],
            len(full_answer),
        )
        try:
            _audit_ask(db, user, ip, conversation_id, question, kb_ids, prepared["no_answer"])
        except Exception as exc:
            logger.warning("qa audit failed conversation=%s: %s", conversation_id, exc)


@router.post("/chat")
def chat(body: ChatRequest, request: Request, db: DBSession, user: CurrentUser):
    """企业知识库问答：默认 SSE 流式返回；stream=false 时一次性 JSON 返回。"""
    # 限流：Redis 滑动窗口，超限 429；Redis 故障时 check_rate_limit 内部降级放行
    if not check_rate_limit(str(user.id)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="提问太频繁，请稍后再试",
        )

    # 会话：conversation_id 缺失时自动创建；存在时校验归属（非本人 403 / 不存在 404）
    if body.conversation_id:
        conv = _get_owned_conversation(db, body.conversation_id, user)
        conversation_id = str(conv.id)
    else:
        conv = Conversation(user_id=user.id, title=DEFAULT_CONVERSATION_TITLE)
        db.add(conv)
        db.commit()
        db.refresh(conv)
        conversation_id = str(conv.id)

    # 知识库范围：kb_ids 与可见集合求交（不越权）；未提供则检索全部可见 KB
    if body.kb_ids:
        visible = set(get_visible_kb_ids(db, user))
        kb_ids = [kb_id for kb_id in body.kb_ids if kb_id in visible]
    else:
        kb_ids = None

    started = time.perf_counter()

    if not body.stream:
        # 一次性 JSON：整个链路（prepare_chat → LLM → 保存）包在同一个 Trace 内
        with trace(
            name="qa.chat",
            run_type="chain",
            inputs={
                "question": body.question,
                "conversation_id": conversation_id,
                "kb_ids": kb_ids,
            },
        ):
            prepared = prepare_chat(
                db,
                user,
                body.question,
                conversation_id=conversation_id,
                kb_ids=kb_ids,
            )
            # 拒答直接下发文案，否则 complete 生成完整回答（附 token 用量）
            if prepared["no_answer"]:
                answer, usage = NO_ANSWER_TEXT, None
            else:
                answer, usage = complete(prepared["messages"])
            save_message(db, conversation_id, "user", body.question)
            assistant_msg = save_message(
                db,
                conversation_id,
                "assistant",
                answer,
                sources=prepared["sources"],
                usage=usage,
            )
            total_ms = (time.perf_counter() - started) * 1000
            _log_timings(
                conversation_id,
                prepared["retrieval_ms"],
                None,
                total_ms,
                prepared["no_answer"],
                len(answer),
            )
            _audit_ask(
                db,
                user,
                _client_ip(request),
                conversation_id,
                body.question,
                kb_ids,
                prepared["no_answer"],
            )
        return ok(
            {
                "answer": answer,
                "sources": prepared["sources"],
                "conversation_id": conversation_id,
                "message_id": str(assistant_msg.id),
                "usage": usage,
            }
        )

    # 流式：prepare_chat 在生成器内执行，检索与 LLM 生成同属一个 Trace
    return StreamingResponse(
        _event_stream(
            db,
            user,
            _client_ip(request),
            body.question,
            conversation_id,
            kb_ids,
            started,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class FeedbackRequest(BaseModel):
    """回答反馈请求体（feedback=None 表示取消评价）。"""

    feedback: str | None = Field(default=None, pattern="^(like|dislike)$")


@router.post("/messages/{message_id}/feedback")
def set_message_feedback(
    message_id: str,
    body: FeedbackRequest,
    db: DBSession,
    user: CurrentUser,
) -> dict:
    """对本人会话中的 assistant 回答点赞/点踩；再次提交同值或 None 取消。"""
    try:
        msg_uuid = uuid_module.UUID(message_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在"
        ) from None
    msg = db.get(Message, msg_uuid)
    if msg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
    conv = db.get(Conversation, msg.conversation_id)
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    if conv.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权操作该消息")
    if msg.role != MessageRole.assistant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅可对 AI 回答评价"
        )
    msg.feedback = body.feedback
    db.commit()
    return ok({"message_id": message_id, "feedback": msg.feedback})
