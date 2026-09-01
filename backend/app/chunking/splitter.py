"""知识切分（Task 5）：md 先按标题切分再递归切分，其余格式递归字符切分；chunk 落库。

chunk 参数取全局配置（settings.CHUNK_SIZE / settings.CHUNK_OVERLAP）。

每个 chunk 保留 metadata：document_id、kb_id、title_path（标题层级拼接）、
page_number、chunk_index、token_count（近似 len(text)）。
"""

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Chunk, KnowledgeBase

# 递归切分分隔符（中英文标点兼顾）
SEPARATORS = ["\n\n", "\n", "。", ".", " ", ""]

# md 标题切分层级
_MD_HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def _recursive_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    """按知识库参数构造递归切分器（overlap 越界时钳制，避免切分器报错）。"""
    overlap = max(0, min(chunk_overlap, chunk_size - 1))
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=SEPARATORS,
        length_function=len,
    )


def _title_path_from_md_meta(meta: dict) -> str | None:
    """由 md 标题切分产生的 h1/h2/h3 metadata 拼接 title_path。"""
    parts = [str(meta[key]) for key in ("h1", "h2", "h3") if meta.get(key)]
    return " > ".join(parts) or None


def split_documents(
    docs: list[Document],
    chunk_size: int,
    chunk_overlap: int,
    *,
    is_markdown: bool = False,
) -> list[dict]:
    """切分解析产出的 Document 列表，返回 [{"content", "title_path", "page_number"}]。

    - md：MarkdownHeaderTextSplitter（# / ## / ###）先切，再 RecursiveCharacterTextSplitter 二次切；
    - 其他：RecursiveCharacterTextSplitter（split_documents 会继承各块的 metadata，
      含解析器写入的 title_path / page_number / sheet）。
    """
    results: list[dict] = []
    if is_markdown:
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=_MD_HEADERS_TO_SPLIT_ON, strip_headers=False
        )
        splitter = _recursive_splitter(chunk_size, chunk_overlap)
        for doc in docs:
            header_docs = header_splitter.split_text(doc.page_content)
            for piece in splitter.split_documents(header_docs):
                results.append(
                    {
                        "content": piece.page_content,
                        "title_path": _title_path_from_md_meta(piece.metadata),
                        "page_number": None,
                    }
                )
    else:
        splitter = _recursive_splitter(chunk_size, chunk_overlap)
        for piece in splitter.split_documents(docs):
            meta = piece.metadata or {}
            results.append(
                {
                    "content": piece.page_content,
                    "title_path": meta.get("title_path"),
                    "page_number": meta.get("page_number"),
                }
            )
    # 过滤空白 chunk
    return [item for item in results if item["content"] and item["content"].strip()]


def split_and_persist(
    db: Session,
    *,
    doc,
    kb: KnowledgeBase,
    parsed_docs: list[Document],
    is_markdown: bool,
) -> list[Chunk]:
    """切分并持久化：先删该文档旧 chunks，再写入新 chunk 行（chunk_index 顺序编号）。

    返回新建的 Chunk ORM 行列表（供向量化阶段使用）。调用方控制最终事务提交。
    """
    items = split_documents(
        parsed_docs,
        settings.CHUNK_SIZE,
        settings.CHUNK_OVERLAP,
        is_markdown=is_markdown,
    )

    db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    rows = [
        Chunk(
            document_id=doc.id,
            content=item["content"],
            title_path=item["title_path"],
            page_number=item["page_number"],
            chunk_index=index,
            token_count=len(item["content"]),
        )
        for index, item in enumerate(items)
    ]
    db.add_all(rows)
    db.flush()
    return rows
