"""业务异常与全局异常处理器。"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.log import get_logger

logger = get_logger(__name__)


class BizError(Exception):
    """业务异常：HTTP 状态保持 200，通过响应体中的 code 区分错误类型。

    401/403 等认证授权错误由后续模块通过 HTTPException 抛出，不走此异常。
    """

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError) -> JSONResponse:
        logger.warning("biz error: code=%s message=%s path=%s", exc.code, exc.message, request.url.path)
        return JSONResponse(
            status_code=200,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """HTTPException（含 401/403）统一响应结构：code=HTTP 状态码。"""
        logger.warning("http error: status=%s detail=%s path=%s", exc.status_code, exc.detail, request.url.path)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "data": None},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled exception: %s path=%s", exc, request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "Internal Server Error", "data": None},
        )
