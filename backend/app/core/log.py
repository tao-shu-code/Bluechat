"""结构化 JSON 日志配置。"""
import json
import logging
import sys
from typing import Any

from app.core.config import settings


class JsonFormatter(logging.Formatter):
    """将日志记录序列化为单行 JSON，便于日志采集与检索。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def setup_logging() -> None:
    """初始化根日志器（幂等，重复调用不会重复挂载 handler）。"""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL.upper())
    root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取使用 JSON 格式化输出的 logger。"""
    setup_logging()
    return logging.getLogger(name)
