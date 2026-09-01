"""QA 提问限流（Task 9.3）：Redis zset 滑动窗口。

- key 形如 qa:rate:{user_id}，member 为纳秒时间戳字符串（保证唯一），score 为秒级时间戳；
- 每次：先清除窗口外时间戳，再统计窗口内请求数，超过上限返回 False（未超限才写入本次时间戳）；
- Redis 故障时降级放行（限流不阻断业务），仅记 warning 日志。

离线测试可 monkeypatch 本模块的 get_redis 为 fake 客户端（需实现 pipeline/zcard/zadd/expire）。
"""

import time
import uuid

from app.core.config import settings
from app.core.log import get_logger
from app.session.redis_cache import get_redis

logger = get_logger(__name__)

RATE_LIMIT_KEY_PREFIX = "qa:rate"
WINDOW_SECONDS = 60


def _rate_key(user_id: str) -> str:
    """限流窗口 key。"""
    return f"{RATE_LIMIT_KEY_PREFIX}:{user_id}"


def check_rate_limit(user_id: str, limit: int | None = None) -> bool:
    """滑动窗口限流检查：放行返回 True，窗口内请求数达到上限返回 False。

    - limit 缺省取 settings.RATE_LIMIT_PER_MIN；
    - 被拒绝的请求不写入时间戳（不占窗口名额）；
    - Redis 不可用时降级放行。
    """
    max_requests = settings.RATE_LIMIT_PER_MIN if limit is None else limit
    key = _rate_key(user_id)
    now = time.time()
    try:
        client = get_redis()
        pipe = client.pipeline()
        pipe.zremrangebyscore(key, "-inf", now - WINDOW_SECONDS)
        pipe.zcard(key)
        count = int(pipe.execute()[1])
        if count >= max_requests:
            logger.info(
                "qa rate limited user=%s count=%s limit=%s", user_id, count, max_requests
            )
            return False
        # member 用 uuid 保证唯一：同 tick 内并发请求的纳秒时间戳可能相同
        # （Windows 上 time.time_ns() 精度约 15.6ms），member 冲突会被 zadd 覆盖导致计数偏少
        client.zadd(key, {uuid.uuid4().hex: now})
        client.expire(key, WINDOW_SECONDS + 5)
        return True
    except Exception as exc:
        logger.warning("qa rate limit check degraded (allow): %s", exc)
        return True
