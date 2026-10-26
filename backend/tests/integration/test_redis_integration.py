"""真实 Redis 集成：命令/TTL、并发原子性、限流服务、连接恢复。

运行前提：JIANSHU_REDIS_TEST_URL 指向本机隔离实例（db ≥ 10）。
键前缀 jianshu-int:，teardown 只清理该前缀。
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("JIANSHU_REDIS_TEST_URL"),
    reason="需要注入本机隔离 Redis（JIANSHU_REDIS_TEST_URL，db ≥ 10）",
)

from tests.integration.conftest import KEY_PREFIX  # noqa: E402


@pytest.mark.asyncio
async def test_redis_commands_and_ttl(real_redis):
    key = f"{KEY_PREFIX}kv"
    await real_redis.set(key, "值-1", ex=60)
    assert await real_redis.get(key) == "值-1"
    ttl = await real_redis.ttl(key)
    assert 0 < ttl <= 60
    assert await real_redis.delete(key) == 1
    assert await real_redis.get(key) is None


@pytest.mark.asyncio
async def test_redis_concurrent_incr_atomicity(real_redis):
    key = f"{KEY_PREFIX}incr"

    async def bump():
        return await real_redis.incr(key)

    values = await asyncio.gather(*(bump() for _ in range(50)))
    assert sorted(values) == list(range(1, 51))  # 原子自增无丢失/重复
    assert int(await real_redis.get(key)) == 50


@pytest.mark.asyncio
async def test_rate_limit_service_with_real_redis(real_redis):
    import uuid

    from fastapi import HTTPException

    from app.services.rate_limit import check_user_rate_limit

    user_id = uuid.uuid4().int % 1_000_000  # 唯一 id：键不跨运行残留
    for _ in range(3):
        await check_user_rate_limit(real_redis, user_id=user_id, action=f"{KEY_PREFIX}act", limit=3, window_seconds=30)
    with pytest.raises(HTTPException) as exc:
        await check_user_rate_limit(real_redis, user_id=user_id, action=f"{KEY_PREFIX}act", limit=3, window_seconds=30)
    assert exc.value.status_code == 429
    ttl = await real_redis.ttl(f"ratelimit:{KEY_PREFIX}act:{user_id}")
    assert 0 < ttl <= 30


@pytest.mark.asyncio
async def test_redis_connection_recovery(real_redis):
    """客户端断连重连后可继续服务（同一 URL 重建客户端）。"""
    from redis.asyncio import Redis

    from tests.integration.conftest import REDIS_URL

    key = f"{KEY_PREFIX}recovery"
    await real_redis.set(key, "alive", ex=60)
    await real_redis.aclose()  # 主动断开

    revived = Redis.from_url(REDIS_URL, decode_responses=True)
    assert await revived.get(key) == "alive"  # 键仍在（持久于服务端），恢复即可读
    await revived.delete(key)
    await revived.aclose()
