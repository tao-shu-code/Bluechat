"""FastAPI 应用入口。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.common.exceptions import register_exception_handlers
from app.core.log import get_logger
from app.document.document_router import router as document_router
from app.document.kb_router import router as kb_router
from app.qa.router import router as qa_router
from app.retrieval.router import router as retrieval_router
from app.session.router import router as session_router

logger = get_logger(__name__)

CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:3000",
]


def create_app() -> FastAPI:
    """应用工厂。"""
    app = FastAPI(title="AI Knowledge Base API", version="0.1.0")

    # 跨域配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 全局异常处理（BizError -> HTTP 200 + code；未知异常 -> 500 统一结构）
    register_exception_handlers(app)

    @app.on_event("startup")
    def _run_migrations() -> None:
        """存量库幂等迁移（尽力而为，失败仅告警不阻断启动）。"""
        from app.models.migrations import drop_kb_chunk_columns, ensure_message_columns

        try:
            ensure_message_columns()
            drop_kb_chunk_columns()
        except Exception as exc:
            logger.warning("startup migration failed (ignored): %s", exc)

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        """健康检查。"""
        return {"status": "ok"}

    # 认证与用户模块（登录 / 登出 / 当前用户）
    app.include_router(auth_router)

    # 系统管理（用户 / 部门 / 角色，仅 ADMIN，供权限管理界面使用）
    app.include_router(admin_router)

    # 知识库管理与文档入库流水线（上传 → 解析 → 切分 → 向量化）
    app.include_router(kb_router)
    app.include_router(document_router)

    # 会话管理（创建 / 列表 / 详情 / 删除，含 Redis 会话上下文缓存）
    app.include_router(session_router)

    # LLM 问答（默认 SSE 流式，兼容一次性 JSON；含限流 / 拒答 / 审计）
    app.include_router(qa_router)

    # 文档检索调试（pg_search BM25 / 向量，top-K 可选）
    app.include_router(retrieval_router)

    return app


app = create_app()
