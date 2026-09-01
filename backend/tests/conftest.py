"""pytest 共享配置。

- sys.path 处理：保证以任意工作目录运行 pytest 时 `app` 包均可导入；
- 单元测试不依赖真实中间件（PostgreSQL / Redis / MinIO / RabbitMQ / LLM / Rerank API）：
  涉及外部服务的逻辑一律通过 monkeypatch / stub 替换（见各测试文件）。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
