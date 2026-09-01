"""QA 限流单元测试（Task 9.3）：滑动窗口放行/拒绝/窗口过期恢复/Redis 故障降级。

不依赖真实 Redis：monkeypatch rate_limit.get_redis 为 fake 客户端，
时间用 monkeypatch 替换 rate_limit.time 以模拟窗口滑动。
"""

from types import SimpleNamespace

import pytest

import app.qa.rate_limit as rate_limit


class FakePipeline:
    """按调用顺序执行并返回结果，execute()[1] 对应 zcard（与真实调用序列一致）。"""

    def __init__(self, client: "FakeRedis"):
        self._client = client
        self._ops: list[tuple] = []

    def zremrangebyscore(self, key, min_score, max_score):
        self._ops.append(("zremrangebyscore", key, min_score, max_score))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def execute(self):
        results = []
        for op in self._ops:
            if op[0] == "zremrangebyscore":
                _, key, _min_score, max_score = op
                members = self._client.zsets.setdefault(key, {})
                expired = [m for m, score in members.items() if score <= max_score]
                for member in expired:
                    del members[member]
                results.append(len(expired))
            elif op[0] == "zcard":
                results.append(len(self._client.zsets.get(op[1], {})))
        return results


class FakeRedis:
    def __init__(self):
        self.zsets: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def expire(self, key, ttl):
        self.ttls[key] = ttl


@pytest.fixture()
def fake_redis(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: client)
    return client


class TestSlidingWindow:
    def test_allows_n_then_rejects(self, fake_redis):
        for _ in range(3):
            assert rate_limit.check_rate_limit("u1", limit=3) is True
        assert rate_limit.check_rate_limit("u1", limit=3) is False
        # 被拒绝的请求不写入窗口（不占名额）
        assert len(fake_redis.zsets["qa:rate:u1"]) == 3

    def test_rejected_request_not_recorded(self, fake_redis):
        assert rate_limit.check_rate_limit("u1", limit=1) is True
        assert rate_limit.check_rate_limit("u1", limit=1) is False
        assert rate_limit.check_rate_limit("u1", limit=1) is False
        assert len(fake_redis.zsets["qa:rate:u1"]) == 1

    def test_window_expiry_restores_quota(self, fake_redis, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(rate_limit, "time", SimpleNamespace(time=lambda: now[0]))
        for _ in range(2):
            assert rate_limit.check_rate_limit("u2", limit=2) is True
        assert rate_limit.check_rate_limit("u2", limit=2) is False
        # 时间推进跨过窗口（60s），旧时间戳被清理，名额恢复
        now[0] += rate_limit.WINDOW_SECONDS + 1
        assert rate_limit.check_rate_limit("u2", limit=2) is True

    def test_users_isolated(self, fake_redis):
        assert rate_limit.check_rate_limit("ua", limit=1) is True
        assert rate_limit.check_rate_limit("ua", limit=1) is False
        assert rate_limit.check_rate_limit("ub", limit=1) is True

    def test_expire_set_on_write(self, fake_redis):
        rate_limit.check_rate_limit("u3", limit=5)
        assert fake_redis.ttls["qa:rate:u3"] == rate_limit.WINDOW_SECONDS + 5


class TestDegradation:
    def test_redis_connect_failure_allows(self, monkeypatch):
        def _boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr(rate_limit, "get_redis", _boom)
        # Redis 故障时降级放行，不抛异常
        assert rate_limit.check_rate_limit("u4", limit=1) is True

    def test_pipeline_failure_allows(self, fake_redis, monkeypatch):
        def _broken_pipeline():
            raise RuntimeError("pipeline broken")

        monkeypatch.setattr(fake_redis, "pipeline", _broken_pipeline)
        assert rate_limit.check_rate_limit("u5", limit=1) is True

    def test_key_prefix(self):
        assert rate_limit._rate_key("abc") == "qa:rate:abc"
