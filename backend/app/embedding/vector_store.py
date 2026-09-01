"""向量化与索引（Task 6）：OpenAI 兼容 Embeddings（批量重试）+ PGVector 向量存储。

向量存储选型（运行时自动判定，导入顺序即优先级）：
1. langchain-postgres 的 PGVector（主选，本环境已验证与 langchain 1.3.16 兼容）：
   - connection 使用 postgresql+psycopg://（psycopg3 驱动，注意与业务库的 psycopg2 驱动不同）；
   - collection_name=f"kb_{kb_id}"，use_jsonb=True，embedding_length=settings.EMBEDDING_DIM
     （固定维度，使 HNSW 索引可创建）；
2. 回退 langchain_community.vectorstores.PGVector（psycopg2 驱动，community 已停止维护）。

两个后端的表结构一致（langchain_pg_collection / langchain_pg_embedding，
cmetadata 为 JSON/JSONB），因此按 document_id 删除向量统一走原生 SQL。
"""

import time
from concurrent.futures import ThreadPoolExecutor

from langsmith import traceable
from sqlalchemy import text as sql_text

from app.core.config import settings
from app.core.database import engine
from app.core.log import get_logger

logger = get_logger(__name__)

# pgvector 两张默认表名（langchain-postgres 与 community PGVector 均使用）
COLLECTION_TABLE = "langchain_pg_collection"
EMBEDDING_TABLE = "langchain_pg_embedding"

_BACKEND: str = "none"

try:  # 1) 主选：langchain-postgres
    from langchain_postgres import PGVector as _PGVector  # noqa: N811

    _BACKEND = "langchain-postgres"
except Exception:  # pragma: no cover - 回退路径
    try:  # 2) 回退：community PGVector
        from langchain_community.vectorstores import PGVector as _PGVector  # noqa: N811

        _BACKEND = "langchain-community"
    except Exception as exc:
        _PGVector = None  # type: ignore[assignment]
        logger.error("no PGVector backend available: %s", exc)

VECTOR_BACKEND: str = _BACKEND


def _psycopg_connection_url() -> str:
    """langchain-postgres 需要 psycopg(3) 驱动：postgresql+psycopg2:// → postgresql+psycopg://。"""
    return settings.DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+psycopg://")


def collection_name_for(kb_id: str) -> str:
    """每个知识库一个向量 collection。"""
    return f"kb_{kb_id}"


# 进程级缓存：复用实例避免每次检索重复"新建 client + 发探测请求"（省一次 API 往返）
_EMBEDDINGS = None


def get_embeddings():
    """OpenAI 兼容 Embeddings（进程级单例）；check_embedding_ctx_length=False 以兼容
    非 OpenAI 的兼容服务（如 bge-m3，直接发送原始文本而非 tiktoken token）。

    dimensions 参数：请求端显式指定输出维度（OpenAI text-embedding-3 系列、
    SiliconFlow Qwen3-Embedding 等支持）；bge-m3 等不支持 dimensions 的模型
    会返回 4xx，此时降级为不带 dimensions 重新请求。"""
    global _EMBEDDINGS
    if _EMBEDDINGS is not None:
        return _EMBEDDINGS

    from langchain_openai import OpenAIEmbeddings

    try:
        emb = OpenAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            api_key=settings.EMBEDDING_API_KEY or "empty",
            base_url=settings.EMBEDDING_API_BASE or None,
            check_embedding_ctx_length=False,
            dimensions=settings.EMBEDDING_DIM,
            timeout=60,  # 供应商偶发长尾抖动（实测单次可达数秒~数十秒），超时快速失败
            max_retries=2,
        )
        # 探测请求：确认服务端接受 dimensions 参数（仅首次实例化时执行）
        emb.embed_documents(["dim probe"])
        _EMBEDDINGS = emb
        return emb
    except Exception as exc:
        if "dimension" not in str(exc).lower():
            raise  # 非 dimensions 参数问题（网络/鉴权等），向上抛
        logger.warning(
            "embedding endpoint rejected dimensions=%s (%s), retry without it",
            settings.EMBEDDING_DIM,
            exc,
        )
        _EMBEDDINGS = OpenAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            api_key=settings.EMBEDDING_API_KEY or "empty",
            base_url=settings.EMBEDDING_API_BASE or None,
            check_embedding_ctx_length=False,
            timeout=60,
            max_retries=2,
        )
        return _EMBEDDINGS


def get_vector_store(kb_id: str):
    """获取指定知识库的 PGVector 实例（collection 维度按 settings.EMBEDDING_DIM）。"""
    if _PGVector is None:
        raise RuntimeError("未安装可用的 PGVector 向量存储后端")
    embeddings = get_embeddings()
    if _BACKEND == "langchain-postgres":
        return _PGVector(
            embeddings=embeddings,
            collection_name=collection_name_for(kb_id),
            connection=_psycopg_connection_url(),
            embedding_length=settings.EMBEDDING_DIM,
            use_jsonb=True,
        )
    return _PGVector(
        embedding_function=embeddings,
        collection_name=collection_name_for(kb_id),
        connection_string=settings.DATABASE_URL,
    )


def _embed_batch_with_retry(
    embeddings, batch: list[str], *, max_retries: int = 3
) -> list[list[float]]:
    """单批嵌入 + 指数退避重试，全部失败时抛最后一个异常。"""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return embeddings.embed_documents(batch)
        except Exception as exc:
            last_exc = exc
            wait = 2**attempt
            logger.warning(
                "embedding batch failed (attempt=%s/%s, retry in %ss): %s",
                attempt + 1,
                max_retries,
                wait,
                exc,
            )
            time.sleep(wait)
    raise last_exc


def embed_texts_with_retry(
    texts: list[str], *, batch_size: int | None = None, max_retries: int = 3
) -> list[list[float]]:
    """分批嵌入：批间并行（EMBEDDING_MAX_CONCURRENCY 限流），批内失败独立重试。

    返回顺序与输入 texts 一致；任一批重试耗尽后仍失败则抛异常
    （由调用方将文档置为 FAILED，可通过重试接口重新入队）。
    """
    embeddings = get_embeddings()
    size = batch_size or settings.EMBEDDING_BATCH_SIZE
    batches = [texts[start : start + size] for start in range(0, len(texts), size)]
    if not batches:
        return []
    if len(batches) == 1:
        return _embed_batch_with_retry(embeddings, batches[0], max_retries=max_retries)

    # 批间并行：嵌入为外部 IO，等待占比高；并发数受 EMBEDDING_MAX_CONCURRENCY 保护
    workers = min(settings.EMBEDDING_MAX_CONCURRENCY, len(batches))
    batch_results: list[list[list[float]]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_embed_batch_with_retry, embeddings, b, max_retries=max_retries)
            for b in batches
        ]
        for future in futures:
            batch_results.append(future.result())
    return [vec for batch_vectors in batch_results for vec in batch_vectors]


def _chunk_metadata(chunk, kb_id: str) -> dict:
    """chunk 落向量库的 metadata（与 chunks 表字段对应）。"""
    return {
        "document_id": str(chunk.document_id),
        "kb_id": kb_id,
        "title_path": chunk.title_path,
        "page_number": chunk.page_number,
        "chunk_index": chunk.chunk_index,
        "token_count": chunk.token_count,
    }


@traceable(name="embedding.embed_chunks", hide_inputs=["chunks"])
def embed_chunks(kb_id: str, chunks: list) -> int:
    """批量向量化 chunk 并写入 PGVector（先本地批量重试嵌入，再 add_embeddings）。"""
    if not chunks:
        return 0
    store = get_vector_store(kb_id)
    texts = [chunk.content for chunk in chunks]
    metadatas = [_chunk_metadata(chunk, kb_id) for chunk in chunks]
    vectors = embed_texts_with_retry(texts)
    # 两个后端的 add_embeddings 签名一致：(texts, embeddings, metadatas=None, ids=None)
    store.add_embeddings(texts, embeddings=vectors, metadatas=metadatas)
    logger.info("embedded chunks=%s kb=%s backend=%s", len(chunks), kb_id, _BACKEND)
    return len(chunks)


def delete_for_document(kb_id: str, document_id: str) -> int:
    """按 document_id 删除向量（原生 SQL：collection 内 cmetadata->>'document_id' 匹配）。"""
    with engine.begin() as conn:
        result = conn.execute(
            sql_text(
                f"DELETE FROM {EMBEDDING_TABLE} e USING {COLLECTION_TABLE} c "
                "WHERE e.collection_id = c.uuid "
                "AND c.name = :collection "
                "AND e.cmetadata->>'document_id' = :document_id"
            ),
            {"collection": collection_name_for(kb_id), "document_id": document_id},
        )
    deleted = result.rowcount or 0
    logger.info("deleted vectors document=%s kb=%s rows=%s", document_id, kb_id, deleted)
    return deleted


def drop_collection(kb_id: str) -> None:
    """删除整个知识库的向量 collection（embedding 行随 FK CASCADE 删除）。"""
    with engine.begin() as conn:
        conn.execute(
            sql_text(f"DELETE FROM {COLLECTION_TABLE} WHERE name = :collection"),
            {"collection": collection_name_for(kb_id)},
        )
    logger.info("dropped vector collection kb=%s", kb_id)
