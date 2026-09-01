"""文档处理 Celery 任务（Task 4）：解析 → 切分 → 嵌入 → READY。

- parse_document(document_id)：从 MinIO 拉文件解析（供上传 / 失败重试调用）；
- rechunk_and_embed(document_id)：重建索引，重新走 切分→嵌入 全流程（原始文件保留，
  解析作为获取文本的内部步骤静默重做，供 reindex 接口调用）。

约定：每个任务自建 DB session（SessionLocal），任务结束确保 commit/rollback；
task_acks_late 已在 celery_app 开启；失败置 FAILED + error_message（traceback 摘要），
不向上抛出（避免 acks_late 下的重复投递循环），返回失败摘要字符串。
"""

import traceback
import uuid as uuid_module

from langsmith import traceable

from app.chunking.splitter import split_and_persist
from app.core.database import SessionLocal
from app.core.log import get_logger
from app.document.minio_client import get_minio
from app.document.parsers import parse_file
from app.embedding.index_sql import ensure_hnsw
from app.embedding.vector_store import embed_chunks
from app.models import Document, DocumentStatus, KnowledgeBase
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

_ERROR_SUMMARY_MAX = 1500


@celery_app.task(name="document.parse_document")
@traceable(name="pipeline.parse_document", hide_inputs=["self"])
def parse_document(document_id: str) -> str:
    """解析任务：状态 UPLOADED→PARSING，拉取 MinIO 文件并解析，随后进入切分/嵌入。

    仅接受 UPLOADED / FAILED 状态（失败重试从解析阶段开始，保留已上传原文件）。
    """
    return _execute(document_id, from_parse=True)


@celery_app.task(name="document.rechunk_and_embed")
@traceable(name="pipeline.rechunk_and_embed", hide_inputs=["self"])
def rechunk_and_embed(document_id: str) -> str:
    """重建索引：重新走 切分→嵌入 全流程（状态 CHUNKING→EMBEDDING→READY）。"""
    return _execute(document_id, from_parse=False)


def _error_summary() -> str:
    """traceback 摘要（尾部截断，控制 error_message 长度）。"""
    tb = traceback.format_exc()
    return tb[-_ERROR_SUMMARY_MAX:]


def _mark_failed(db, document_id: str, message: str) -> None:
    """置 FAILED + 错误信息（独立小事务，尽力而为）。"""
    try:
        doc = db.get(Document, _to_uuid(document_id))
        if doc is not None:
            doc.status = DocumentStatus.FAILED
            doc.error_message = message[:_ERROR_SUMMARY_MAX]
            db.commit()
    except Exception:
        db.rollback()
        logger.error("mark document %s FAILED failed", document_id, exc_info=True)


def _to_uuid(value: str):
    return uuid_module.UUID(value)


def _execute(document_id: str, *, from_parse: bool) -> str:
    """任务执行主体：自建 session，全链路 解析(可选)→切分→嵌入→READY。"""
    db = SessionLocal()
    try:
        doc = db.get(Document, _to_uuid(document_id))
        if doc is None:
            logger.error("document %s not found", document_id)
            return f"document {document_id} not found"

        if from_parse and doc.status not in (DocumentStatus.UPLOADED, DocumentStatus.FAILED):
            return f"skip: document {document_id} status={doc.status}"

        kb = db.get(KnowledgeBase, doc.kb_id)
        if kb is None:
            _mark_failed(db, document_id, "所属知识库不存在")
            return f"failed: kb {doc.kb_id} not found"

        # ----- 解析阶段（reindex 时静默重做，不回退状态） -----
        if from_parse:
            doc.status = DocumentStatus.PARSING
            doc.error_message = None
            db.commit()

        content = get_minio().get_object(doc.object_key)
        parsed_docs = parse_file(content, doc.filename)

        # ----- 切分阶段 -----
        doc.status = DocumentStatus.CHUNKING
        db.commit()
        chunk_rows = split_and_persist(
            db,
            doc=doc,
            kb=kb,
            parsed_docs=parsed_docs,
            is_markdown=(doc.file_type == "md"),
        )
        db.commit()

        # ----- 嵌入阶段 -----
        doc.status = DocumentStatus.EMBEDDING
        db.commit()
        embed_chunks(str(kb.id), chunk_rows)
        ensure_hnsw()  # 首次入库后确保 HNSW 索引（幂等、容错）

        doc.status = DocumentStatus.READY
        doc.error_message = None
        db.commit()
        logger.info("document %s processed to READY (chunks=%s)", document_id, len(chunk_rows))
        return "ok"
    except Exception as exc:
        logger.error("document task failed: %s", document_id, exc_info=exc)
        db.rollback()
        _mark_failed(db, document_id, _error_summary())
        return f"failed: {exc}"
    finally:
        db.close()
