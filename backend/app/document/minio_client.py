"""MinIO 对象存储客户端封装（模块级单例）。

- ensure_bucket：bucket 不存在则创建，并开启 SSE-S3 服务端加密；
- put_object / get_object / delete_object：以 object_key（bucket 内对象名）为操作单元；
- delete_prefix / list_prefix：用于知识库/文档删除时的级联清理。
"""

import io
import threading

from minio import Minio

from app.core.config import settings
from app.core.log import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()
_client: "MinioClient | None" = None


class MinioClient:
    """minio-py 的薄封装，统一在方法内部处理 bucket 与前缀等约定。"""

    def __init__(self) -> None:
        self._minio = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self._bucket = settings.MINIO_BUCKET
        self._bucket_ready = False

    # ----- bucket 管理 -----

    def ensure_bucket(self) -> None:
        """bucket 不存在则创建，并开启 SSE-S3 服务端加密（幂等，容错）。"""
        if self._bucket_ready:
            return
        with _lock:
            if self._bucket_ready:
                return
            if not self._minio.bucket_exists(self._bucket):
                self._minio.make_bucket(self._bucket)
                logger.info("MinIO bucket created: %s", self._bucket)
            try:
                # SSE-S3：S3 托管密钥的服务端加密（AES256 规则）
                from minio.sseconfig import Rule, SSEConfig

                self._minio.set_bucket_encryption(
                    self._bucket, SSEConfig(Rule.new_sse_s3_rule())
                )
            except Exception as exc:  # 加密配置失败不阻断主流程（旧版服务端可能不支持）
                logger.warning("set bucket encryption failed (ignored): %s", exc)
            self._bucket_ready = True

    @property
    def bucket(self) -> str:
        return self._bucket

    # ----- 对象读写 -----

    def put_object(self, object_key: str, data: bytes, content_type: str | None = None) -> str:
        """上传对象，返回 object_key。"""
        self.ensure_bucket()
        self._minio.put_object(
            self._bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
        return object_key

    def get_object(self, object_key: str) -> bytes:
        """读取对象内容（整体读入内存，调用方负责及时释放）。"""
        self.ensure_bucket()
        resp = self._minio.get_object(self._bucket, object_key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def delete_object(self, object_key: str) -> None:
        """删除单个对象（幂等：对象不存在不报错）。"""
        self.ensure_bucket()
        self._minio.remove_object(self._bucket, object_key)

    # ----- 级联清理 -----

    def list_prefix(self, prefix: str) -> list[str]:
        """列出指定前缀下的所有 object_key。"""
        self.ensure_bucket()
        return [
            obj.object_name
            for obj in self._minio.list_objects(self._bucket, prefix=prefix, recursive=True)
            if obj.object_name
        ]

    def delete_prefix(self, prefix: str) -> int:
        """删除指定前缀下全部对象，返回删除数量（尽力而为，异常不向上抛）。"""
        deleted = 0
        try:
            for key in self.list_prefix(prefix):
                self.delete_object(key)
                deleted += 1
        except Exception as exc:
            logger.warning("delete prefix %s partially failed: %s", prefix, exc)
        return deleted


def get_minio() -> MinioClient:
    """获取进程内唯一的 MinioClient 实例。"""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = MinioClient()
    return _client
