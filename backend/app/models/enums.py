"""与 02_schema.sql 中 PostgreSQL 原生枚举一一对应的 Python 枚举。"""

from enum import Enum


class KbVisibility(str, Enum):
    """知识库可见性（PG 枚举 kb_visibility）。"""

    ALL = "ALL"
    DEPARTMENT = "DEPARTMENT"
    USER = "USER"


class DocumentStatus(str, Enum):
    """文档处理状态（PG 枚举 document_status）。"""

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    READY = "READY"
    FAILED = "FAILED"


class MessageRole(str, Enum):
    """消息角色（PG 枚举 message_role）。"""

    user = "user"
    assistant = "assistant"
