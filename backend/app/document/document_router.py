"""文档接口（Task 3）：上传 / 列表 / 重试 / 重建索引 / 删除。

权限：上传/删除/重试/重建仅 ADMIN / KNOWLEDGE_MANAGER，且目标 KB 需可见；
文档列表需目标 KB 可见。上传与删除写审计日志（action=upload / delete）。
"""

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from app.common.audit import audit_log
from app.common.deps import CurrentUser, DBSession, get_visible_kb_ids, require_roles, user_role_codes
from app.common.exceptions import BizError
from app.common.response import ok
from app.core.config import settings
from app.core.log import get_logger
from app.document.minio_client import get_minio
from app.embedding.vector_store import delete_for_document
from app.models import Document, DocumentStatus, KnowledgeBase, User
from app.tasks.document_tasks import parse_document, rechunk_and_embed

logger = get_logger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

KB_MANAGE_ROLES = ("ADMIN", "KNOWLEDGE_MANAGER")

# 上传允许的扩展名（.doc 通过上传校验，解析阶段会明确报"不支持 .doc"）
ALLOWED_EXTS = {"pdf", "doc", "docx", "xls", "xlsx", "txt", "md"}

# BizError 业务码约定：4001 参数错误 / 4004 资源不存在 / 4009 状态冲突 / 1002 依赖服务失败
CODE_PARAM_INVALID = 4001
CODE_DOC_NOT_FOUND = 4004
CODE_STATE_INVALID = 4009
CODE_ENQUEUE_FAILED = 1002

_PROCESSING_STATUSES = {
    DocumentStatus.PARSING,
    DocumentStatus.CHUNKING,
    DocumentStatus.EMBEDDING,
}


def _safe_filename(raw: str | None) -> str:
    """取路径最后一段，防目录穿越。"""
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    return name if name not in ("", ".", "..") else ""


def _file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _get_kb_or_raise(db, kb_id: str) -> KnowledgeBase:
    try:
        kb_uuid = uuid.UUID(kb_id)
    except ValueError:
        raise BizError(CODE_DOC_NOT_FOUND, "知识库不存在") from None
    kb = db.get(KnowledgeBase, kb_uuid)
    if kb is None:
        raise BizError(CODE_DOC_NOT_FOUND, "知识库不存在")
    return kb


def _ensure_kb_visible(db, user: User, kb_id) -> None:
    """目标知识库需在当前用户可见范围内（ADMIN 可见全部，无需再查）。"""
    if "ADMIN" in user_role_codes(user):
        return
    if str(kb_id) not in get_visible_kb_ids(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该知识库"
        )


def _get_doc_or_raise(db, document_id: str) -> Document:
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise BizError(CODE_DOC_NOT_FOUND, "文档不存在") from None
    doc = db.get(Document, doc_uuid)
    if doc is None:
        raise BizError(CODE_DOC_NOT_FOUND, "文档不存在")
    return doc


def _enqueue_parse(db, document_id: str) -> None:
    """入队解析任务；broker 不可用时回写 FAILED（保证可重试）并抛 BizError。"""
    try:
        parse_document.delay(document_id)
    except Exception as exc:
        logger.error("enqueue parse_document failed: %s", exc)
        doc = db.get(Document, uuid.UUID(document_id))
        if doc is not None:
            doc.status = DocumentStatus.FAILED
            doc.error_message = f"解析任务入队失败：{exc}"
            db.commit()
        raise BizError(CODE_ENQUEUE_FAILED, "解析任务入队失败（消息队列不可用）") from None


def _document_payload(doc: Document) -> dict:
    return {
        "id": str(doc.id),
        "kb_id": str(doc.kb_id),
        "filename": doc.filename,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "status": doc.status.value if hasattr(doc.status, "value") else doc.status,
        "error_message": doc.error_message,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


@router.post("/upload")
def upload_documents(
    request: Request,
    db: DBSession,
    user: Annotated[User, Depends(require_roles(*KB_MANAGE_ROLES))],
    kb_id: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
) -> dict:
    """批量上传文档：逐文件校验格式与大小，成功者入库（UPLOADED）+ 传 MinIO + 入队解析。

    返回逐文件结果列表 [{filename, success, document_id?, reason?}]。
    """
    kb = _get_kb_or_raise(db, kb_id)
    _ensure_kb_visible(db, user, kb.id)

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    results: list[dict] = []
    pending: list[str] = []  # 待入队解析的 document_id

    for upload in files:
        filename = _safe_filename(upload.filename)
        item: dict = {"filename": filename, "success": False, "document_id": None, "reason": None}

        ext = _file_extension(filename)
        if not filename or ext not in ALLOWED_EXTS:
            item["reason"] = "不支持的文件格式，仅支持 pdf/doc/docx/xls/xlsx/txt/md"
            results.append(item)
            continue

        content = upload.file.read()
        if len(content) > max_bytes:
            item["reason"] = f"文件超过大小限制 {settings.MAX_UPLOAD_MB}MB"
            results.append(item)
            continue

        doc = Document(
            kb_id=kb.id,
            filename=filename,
            object_key="",  # flush 拿到 id 后回填
            file_type=ext,
            file_size=len(content),
            status=DocumentStatus.UPLOADED,
            uploaded_by=user.id,
        )
        db.add(doc)
        db.flush()
        doc.object_key = f"{kb.id}/{doc.id}/{filename}"

        try:
            get_minio().put_object(doc.object_key, content, content_type=upload.content_type)
        except Exception as exc:
            logger.error("minio put_object failed for %s: %s", filename, exc)
            db.delete(doc)
            item["reason"] = "文件存储失败（对象存储不可用）"
            results.append(item)
            continue

        item.update(success=True, document_id=str(doc.id))
        results.append(item)
        pending.append(str(doc.id))

    db.commit()

    for document_id in pending:
        try:
            _enqueue_parse(db, document_id)
        except BizError:
            for r in results:
                if r.get("document_id") == document_id:
                    r["success"] = False
                    r["reason"] = "解析任务入队失败（消息队列不可用）"

    audit_log(
        db,
        user.id,
        "upload",
        detail={"kb_id": str(kb.id), "results": results},
        ip=_client_ip(request),
    )
    return ok(results)


@router.get("")
def list_documents(
    db: DBSession,
    user: CurrentUser,
    kb_id: str,
    page: int = 1,
    size: int = 20,
) -> dict:
    """文档列表（分页，含状态与错误信息；需 KB 可见）。"""
    kb = _get_kb_or_raise(db, kb_id)
    _ensure_kb_visible(db, user, kb.id)

    page = max(1, page)
    size = min(max(1, size), 100)

    total = db.scalar(
        select(func.count()).select_from(Document).where(Document.kb_id == kb.id)
    )
    rows = db.scalars(
        select(Document)
        .where(Document.kb_id == kb.id)
        .order_by(Document.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return ok(
        {
            "total": total or 0,
            "page": page,
            "size": size,
            "items": [_document_payload(doc) for doc in rows],
        }
    )


@router.post("/{document_id}/retry")
def retry_document(
    db: DBSession,
    user: Annotated[User, Depends(require_roles(*KB_MANAGE_ROLES))],
    document_id: str,
) -> dict:
    """失败重试：仅 FAILED 可重试，从解析阶段开始（保留已上传原文件）。"""
    doc = _get_doc_or_raise(db, document_id)
    _ensure_kb_visible(db, user, doc.kb_id)

    if doc.status != DocumentStatus.FAILED:
        raise BizError(CODE_STATE_INVALID, "仅失败（FAILED）状态的文档可重试")

    doc.status = DocumentStatus.UPLOADED
    db.commit()
    _enqueue_parse(db, str(doc.id))
    return ok({"document_id": str(doc.id), "status": DocumentStatus.UPLOADED.value})


@router.post("/{document_id}/reindex")
def reindex_document(
    db: DBSession,
    user: Annotated[User, Depends(require_roles(*KB_MANAGE_ROLES))],
    document_id: str,
) -> dict:
    """重建索引：重新走 切分→嵌入 全流程（Celery 任务，处理中禁止重复触发）。"""
    doc = _get_doc_or_raise(db, document_id)
    _ensure_kb_visible(db, user, doc.kb_id)

    if doc.status in _PROCESSING_STATUSES:
        raise BizError(CODE_STATE_INVALID, "文档正在处理中，请稍后再试")

    try:
        rechunk_and_embed.delay(str(doc.id))
    except Exception:
        logger.error("enqueue rechunk_and_embed failed: %s", document_id, exc_info=True)
        raise BizError(CODE_ENQUEUE_FAILED, "重建索引任务入队失败（消息队列不可用）") from None
    return ok({"document_id": str(doc.id)})


_PREVIEW_MIME = {
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.get("/{document_id}/preview")
def preview_document(
    db: DBSession,
    user: CurrentUser,
    document_id: str,
    download: bool = False,
) -> Response:
    """源文档预览/下载：从 MinIO 读取原始文件流式返回（读操作，所有登录角色可用）。

    - download=false → inline（浏览器内嵌预览）；download=true → attachment 下载；
    - 文件名按 RFC 5987 用 filename*=UTF-8'' 编码，支持中文。
    """
    doc = _get_doc_or_raise(db, document_id)
    _ensure_kb_visible(db, user, doc.kb_id)

    ext = _file_extension(doc.filename)
    media_type = _PREVIEW_MIME.get(ext, "application/octet-stream")

    try:
        content = get_minio().get_object(doc.object_key)
    except Exception as exc:
        logger.error("minio get_object failed for %s: %s", doc.object_key, exc)
        raise BizError(CODE_DOC_NOT_FOUND, "源文件不存在或对象存储不可用") from None

    disposition = "attachment" if download else "inline"
    encoded_name = quote(doc.filename or "file")
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_name}",
        "Cache-Control": "private, max-age=300",
    }
    return Response(content=content, media_type=media_type, headers=headers)


@router.delete("/{document_id}")
def delete_document(
    request: Request,
    db: DBSession,
    user: Annotated[User, Depends(require_roles(*KB_MANAGE_ROLES))],
    document_id: str,
) -> dict:
    """删除文档：DB 记录（chunks 级联）+ MinIO 对象 + 向量（后两者尽力而为），写审计。"""
    doc = _get_doc_or_raise(db, document_id)
    _ensure_kb_visible(db, user, doc.kb_id)

    kb_id_str = str(doc.kb_id)
    filename = doc.filename
    object_key = doc.object_key

    db.delete(doc)
    db.commit()

    # 尽力而为清理对象存储与向量
    try:
        get_minio().delete_object(object_key)
    except Exception as exc:
        logger.warning("minio delete_object failed for %s: %s", object_key, exc)
    try:
        delete_for_document(kb_id_str, str(doc.id))
    except Exception as exc:
        logger.warning("delete vectors failed for document %s: %s", document_id, exc)

    audit_log(
        db,
        user.id,
        "delete",
        detail={"document_id": str(doc.id), "filename": filename, "kb_id": kb_id_str},
        ip=_client_ip(request),
    )
    return ok({"document_id": str(doc.id)})
