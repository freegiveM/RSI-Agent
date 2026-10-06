import pytest


def test_redis_queue_uses_resp2_without_server():
    redis = pytest.importorskip("redis")
    from rsi_agent.queue import RedisStreamQueue
    queue = RedisStreamQueue.__new__(RedisStreamQueue)
    queue.client = redis.Redis.from_url("redis://127.0.0.1:6379/0", protocol=2)
    assert queue.client.connection_pool.connection_kwargs["protocol"] == 2
