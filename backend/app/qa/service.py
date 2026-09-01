"""RAG 问答编排（Task 9.2）：多轮改写 → 混合检索 → 拒答判定 → Prompt 组装 → 引用来源。

供 app/qa/router.py 调用；LLM 生成（流式/一次性）在 router 层基于本模块输出的
messages 执行。拒答时返回 no_answer=True（max_similarity 低于
settings.RELEVANCE_THRESHOLD），由 router 直接下发固定文案且 sources 为空。
"""

import time
import uuid

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langsmith import traceable
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.log import get_logger
from app.models import Document, User
from app.qa.llm import complete
from app.retrieval.service import hybrid_search
from app.session.router import get_history

logger = get_logger(__name__)

# 拒答文案（前端原样展示）
NO_ANSWER_TEXT = "知识库中暂无相关内容，请换个问法或联系知识管理员"

# 系统提示词：角色设定 + 回答要求 + 资料占位符
SYSTEM_PROMPT = """你是企业知识库助手，请严格依据下方提供的资料回答用户问题。

要求：
1. 仅基于资料内容作答，并在回答中注明所依据的资料编号（如 [资料1]）；
2. 资料未涵盖或你不确定的内容，直接说明不确定，不要编造；
3. 若资料为空或与问题无关，请明确回答：知识库中未找到相关内容；
4. 用简洁、准确、条理清晰的中文回答。

资料：
{context}"""


@traceable(name="qa.rewrite_query", run_type="llm")
def _rewrite_llm_call(prompt: str) -> str:
    """改写 LLM 调用（仅多轮追问才会进入，首问不产生该追踪节点）。

    使用 REWRITE_MODEL（留空回退 LLM_MODEL）：改写是简单任务，主模型为
    思考型时用轻量非思考模型避免拖慢检索前置。
    """
    model = settings.REWRITE_MODEL or None
    text, _ = complete([HumanMessage(content=prompt)], model=model)
    return text.strip().strip("\"'“”‘’")


def rewrite_query(history: list[dict], question: str) -> str:
    """多轮 query 改写：有历史时用 LLM 将「最近历史+当前问题」改写为独立完整的检索 query。

    - 仅当存在历史消息时才调用 LLM 改写，且每次提问最多改写一次；
      首问（历史为空）直接返回原问题，不产生 LLM 调用与追踪节点；
    - 改写失败（LLM 异常 / 空输出）降级返回原问题，不阻断问答链路。
    """
    question = question.strip()
    if not history or not question:
        return question
    recent = history[-(settings.HISTORY_ROUNDS * 2):]
    lines = "\n".join(
        f"{'用户' if item.get('role') == 'user' else '助手'}：{item.get('content', '')}"
        for item in recent
    )
    prompt = (
        "请结合对话历史，把「当前问题」改写为一个不依赖上下文、独立完整、适合知识库检索的问题。\n"
        "要求：\n"
        "1. 结合历史补全代词与省略的主语，保留原意；\n"
        "2. 只输出改写后的问题本身，不要任何解释、前缀或引号；\n"
        "3. 若当前问题已独立完整，原样输出。\n\n"
        f"对话历史：\n{lines}\n\n"
        f"当前问题：{question}\n\n"
        "改写后的问题："
    )
    try:
        rewritten = _rewrite_llm_call(prompt)
    except Exception as exc:
        logger.warning("query rewrite failed, fallback to raw question: %s", exc)
        return question
    return rewritten or question


def _document_names(db: Session, doc_ids: list[str]) -> dict[str, str]:
    """按 document_id 批量查 Document.filename，返回 {document_id: filename}。"""
    uuid_ids: list[uuid.UUID] = []
    for raw in doc_ids:
        try:
            uuid_ids.append(uuid.UUID(raw))
        except (ValueError, AttributeError, TypeError):
            continue
    if not uuid_ids:
        return {}
    rows = db.execute(
        select(Document.id, Document.filename).where(Document.id.in_(uuid_ids))
    ).all()
    return {str(row[0]): row[1] for row in rows}


def _doc_label(meta: dict, document_name: str | None) -> str:
    """资料块标题：优先 title_path 第 1 段，缺省回退文档名。"""
    title_path = str(meta.get("title_path") or "").strip()
    if title_path:
        first = title_path.split(" > ")[0].strip()
        if first:
            return first
    return (document_name or "未知文档").strip() or "未知文档"


def build_context_blocks(chunks: list[dict], name_map: dict[str, str]) -> str:
    """检索结果 → 资料 blocks 文本：[资料i] 文档名(title_path 第1段或文档名) 页码：内容摘要。"""
    blocks: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        meta = chunk.get("metadata") or {}
        label = _doc_label(meta, name_map.get(str(meta.get("document_id") or "")))
        page_number = meta.get("page_number")
        page_part = (
            f" 第{page_number}页"
            if isinstance(page_number, int) and page_number > 0
            else ""
        )
        content = str(chunk.get("content") or "").strip()
        blocks.append(f"[资料{index}] {label}{page_part}：{content}")
    return "\n\n".join(blocks)


def build_messages(
    history: list[dict], question: str, context: str
) -> list[BaseMessage]:
    """组装对话消息：系统提示词（含资料 blocks）+ 最近 HISTORY_ROUNDS 轮历史 + 用户问题。"""
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT.format(context=context))]
    for item in history[-(settings.HISTORY_ROUNDS * 2):]:
        content = str(item.get("content") or "")
        if not content:
            continue
        if item.get("role") == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    messages.append(HumanMessage(content=question))
    return messages


def build_sources(
    db: Session, chunks: list[dict], name_map: dict[str, str] | None = None
) -> list[dict]:
    """从命中的 chunks 生成引用来源列表，与 Prompt 中 [资料N] 编号一一对应。

    每项：{index, document_id, document_name, title_path, page_number, content}
    - index = 资料编号（1 起，与 build_context_blocks 的枚举顺序一致）；
    - 每个 chunk 一条（不做跨块合并），content 为该资料块的原文；
    - document_name 由 Document 表批量查 filename 补齐（name_map 已提供时复用）。
    """
    if not chunks:
        return []
    doc_ids: list[str] = []
    seen_doc_ids: set[str] = set()
    for chunk in chunks:
        document_id = str((chunk.get("metadata") or {}).get("document_id") or "")
        if document_id and document_id not in seen_doc_ids:
            seen_doc_ids.add(document_id)
            doc_ids.append(document_id)
    if name_map is None:
        name_map = _document_names(db, doc_ids)

    sources = []
    for index, chunk in enumerate(chunks, start=1):
        meta = chunk.get("metadata") or {}
        document_id = str(meta.get("document_id") or "")
        sources.append(
            {
                "index": index,
                "document_id": document_id or None,
                "document_name": name_map.get(document_id) or "未知文档",
                "title_path": meta.get("title_path"),
                "page_number": meta.get("page_number"),
                "content": chunk.get("content") or "",
            }
        )
    return sources


@traceable(name="qa.prepare_chat", run_type="chain", hide_inputs=["db", "user"])
def prepare_chat(
    db: Session,
    user: User,
    question: str,
    *,
    conversation_id: str | None,
    kb_ids: list[str] | None,
) -> dict:
    """问答前置编排：历史 → 多轮改写 → 混合检索 → 拒答判定 → 资料 blocks → messages → sources。

    - 检索范围：kb_ids=None 检索全部可见 KB（交集过滤在 hybrid_search 内完成）；
    - max_similarity < settings.RELEVANCE_THRESHOLD 时判定拒答：
      返回 no_answer=True，messages 与 sources 均为空，由 router 下发固定文案；
    - 返回 {"messages", "sources", "no_answer", "rewritten_query", "retrieval_ms"}。
    """
    started = time.perf_counter()
    history = get_history(conversation_id) if conversation_id else []
    rewritten = rewrite_query(history, question)

    result = hybrid_search(db, user, rewritten, kb_ids=kb_ids)
    chunks = result.get("chunks") or []
    max_similarity = float(result.get("max_similarity") or 0.0)
    retrieval_ms = (time.perf_counter() - started) * 1000

    no_answer = max_similarity < settings.RELEVANCE_THRESHOLD
    if no_answer:
        logger.info(
            "qa no answer: max_similarity=%.4f threshold=%.4f query=%r",
            max_similarity,
            settings.RELEVANCE_THRESHOLD,
            rewritten,
        )
        return {
            "messages": [],
            "sources": [],
            "no_answer": True,
            "rewritten_query": rewritten,
            "retrieval_ms": retrieval_ms,
        }

    doc_ids = [
        str((chunk.get("metadata") or {}).get("document_id") or "")
        for chunk in chunks
    ]
    name_map = _document_names(db, [doc_id for doc_id in doc_ids if doc_id])
    context = build_context_blocks(chunks, name_map)
    messages = build_messages(history, question, context)
    sources = build_sources(db, chunks, name_map=name_map)
    logger.info(
        "qa retrieval done: chunks=%s max_similarity=%.4f rewritten=%r retrieval=%.1fms",
        len(chunks),
        max_similarity,
        rewritten,
        retrieval_ms,
    )
    return {
        "messages": messages,
        "sources": sources,
        "no_answer": False,
        "rewritten_query": rewritten,
        "retrieval_ms": retrieval_ms,
    }
