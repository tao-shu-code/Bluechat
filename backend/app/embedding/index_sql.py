"""pgvector HNSW 索引迁移（Task 6）：首次入库后调用，幂等且尽力而为。

- ensure_hnsw(collection_table)：为 embedding 表创建 HNSW（cosine）索引；
  前置条件是 embedding 列必须为固定维度 vector(N)（pgvector 对无维度列拒绝建索引），
  因此先尽力将列维度对齐 settings.EMBEDDING_DIM；
- 索引已存在 / 表尚不存在 / 维度冲突等情形均只记录日志并跳过，不影响主流程。
"""

from sqlalchemy import text as sql_text

from app.core.config import settings
from app.core.database import engine
from app.core.log import get_logger

logger = get_logger(__name__)


def ensure_hnsw(table: str = "langchain_pg_embedding", dim: int | None = None) -> bool:
    """确保 embedding 表存在 HNSW 索引（vector_cosine_ops），返回是否成功。

    容错：索引已存在则直接返回 True；表不存在 / 列维度迁移失败等异常仅告警。
    """
    dim = dim or settings.EMBEDDING_DIM
    index_name = f"idx_{table}_hnsw"
    try:
        with engine.begin() as conn:
            exists = conn.execute(
                sql_text(
                    "SELECT 1 FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = :table AND indexname = :index"
                ),
                {"table": table, "index": index_name},
            ).first()
            if exists:
                return True

            # pgvector 要求索引列有固定维度：尽力将列对齐为 vector(dim)（无数据/同维度时为无操作）
            try:
                conn.execute(
                    sql_text(
                        f"ALTER TABLE {table} "
                        f"ALTER COLUMN embedding TYPE vector({dim}) "
                        f"USING embedding::vector({dim})"
                    )
                )
            except Exception as exc:
                logger.warning(
                    "align embedding column dimension to %s failed, skip HNSW: %s", dim, exc
                )
                return False

            conn.execute(
                sql_text(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table} USING hnsw (embedding vector_cosine_ops)"
                )
            )
        logger.info("HNSW index ensured on %s (dim=%s)", table, dim)
        return True
    except Exception as exc:
        logger.warning("ensure_hnsw failed (best-effort): %s", exc)
        return False
