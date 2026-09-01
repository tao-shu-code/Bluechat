"""全局配置：基于 pydantic-settings，环境变量集中于仓库根目录 .env 管理。"""
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] = 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ----- 数据库 -----
    DATABASE_URL: str = "postgresql+psycopg2://kbase:kbase123@localhost:5432/kbase"
    POSTGRES_USER: str = "kbase"
    POSTGRES_PASSWORD: str = "kbase123"
    POSTGRES_DB: str = "kbase"

    # ----- Redis / RabbitMQ -----
    REDIS_URL: str = "redis://localhost:6379/0"
    RABBITMQ_URL: str = "amqp://kbase:kbase123@localhost:5672//"

    # ----- MinIO -----
    MINIO_ENDPOINT: str = "localhost:9000"
    # 兼容 .env.example / docker compose 的 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD 命名
    MINIO_ACCESS_KEY: str = Field(
        default="minioadmin",
        validation_alias=AliasChoices("MINIO_ACCESS_KEY", "MINIO_ROOT_USER"),
    )
    MINIO_SECRET_KEY: str = Field(
        default="minioadmin",
        validation_alias=AliasChoices("MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"),
    )
    MINIO_BUCKET: str = "kbase-docs"
    MINIO_SECURE: bool = False

    # ----- JWT -----
    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 720

    # ----- LLM -----
    LLM_API_BASE: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"
    # 多轮改写专用模型（留空回退 LLM_MODEL）：改写是简单任务，建议配轻量非思考模型，
    # 避免思考型主模型拖慢每次追问的检索前置
    REWRITE_MODEL: str = ""

    # ----- Embedding -----
    EMBEDDING_API_BASE: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_DIM: int = 1536
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_MAX_CONCURRENCY: int = 4  # 嵌入分批请求的批间并发上限（外部 IO，受供应商限流约束）

    # ----- Rerank -----
    RERANK_ENABLED: bool = False
    RERANK_API_BASE: str = ""
    RERANK_API_KEY: str = ""
    RERANK_MODEL: str = "bge-reranker-v2-m3"
    RERANK_TIMEOUT: float = 5.0

    # ----- PDF OCR（RapidOCR 本地识别，扫描件/页面内图片） -----
    PDF_OCR_ENABLED: bool = True  # 关闭后纯图片 PDF 解析为空（跳过 OCR）

    # ----- 业务参数 -----
    FTS_CONFIG: str = "chinese"
    # 混合检索召回参数：向量路与 BM25 路各自召回 Top-K，RRF 融合后经 Rerank 截断 Top-N
    RETRIEVAL_TOP_K: int = 10  # 兼容保留：未单独配置时的统一召回数
    VECTOR_TOP_K: int = 10  # 向量检索召回数
    KEYWORD_TOP_K: int = 10  # BM25/关键词检索召回数
    RERANK_TOP_N: int = 10  # Rerank（或降级融合排序）后进入 LLM 上下文的最终条数
    # 相似度阈值（0~1）：向量路归一化相似度低于该值时 QA 模块可拒答
    RELEVANCE_THRESHOLD: float = 0.35
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    RATE_LIMIT_PER_MIN: int = 10
    MAX_UPLOAD_MB: int = 50
    HISTORY_ROUNDS: int = 5

    # ----- LangSmith 追踪（可选） -----
    # 配置 LANGCHAIN_API_KEY 后，LangChain 调用与 @traceable 标注的阶段
    # 自动上报到 LangSmith：各阶段耗时、提示词、AI 输出、检索结果等均可查看
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "kbase-qa"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"

    # ----- 日志 -----
    LOG_LEVEL: str = "INFO"


settings = Settings()

# LangChain/langsmith 从进程环境变量读取追踪配置（.env 中的配置导出到 os.environ）
# 注意：TRACING_V2=true 但 API Key 为空属于半配置状态，强制置为 false 防止运行时报错
import os  # noqa: E402

_tracing_enabled = bool(settings.LANGCHAIN_TRACING_V2 and settings.LANGCHAIN_API_KEY)
os.environ["LANGCHAIN_TRACING_V2"] = "true" if _tracing_enabled else "false"
for _key in ("LANGCHAIN_API_KEY", "LANGCHAIN_PROJECT", "LANGCHAIN_ENDPOINT"):
    _value = getattr(settings, _key)
    if _value:
        os.environ.setdefault(_key, str(_value))
