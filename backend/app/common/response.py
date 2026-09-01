"""统一响应封装。"""
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Resp(BaseModel, Generic[T]):
    """统一响应模型：code=0 表示成功。"""

    code: int = 0
    message: str = "ok"
    data: Optional[T] = None


def ok(data: Any = None, message: str = "ok") -> dict:
    """成功响应体。"""
    return {"code": 0, "message": message, "data": data}


def fail(code: int, message: str) -> dict:
    """失败响应体。"""
    return {"code": code, "message": message, "data": None}
