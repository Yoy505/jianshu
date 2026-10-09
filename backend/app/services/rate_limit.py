"""C 端用户级写接口限流：Redis 固定窗口（契约见 docs/api.md「限流与防重复提交」）。

Redis 不可用时放行（记日志），限流是护栏不是业务正确性的一部分；
与热榜缓存同一容错策略。
"""

import logging

from fastapi import HTTPException
from redis.asyncio import Redis

logger = logging.getLogger("app")

LIMIT_DETAIL = "操作过于频繁，请稍后再试"


async def check_user_rate_limit(
    redis: Redis, *, user_id: int, action: str, limit: int, window_seconds: int = 60
) -> None:
    """超限抛 429；Redis 故障时放行。key: ratelimit:{action}:{user_id}"""
    key = f"ratelimit:{action}:{user_id}"
    try:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window_seconds)
        if current > limit:
            raise HTTPException(status_code=429, detail=LIMIT_DETAIL)
    except HTTPException:
        raise
    except Exception as exc:  # Redis 不可用：放行不阻断业务
        logger.warning("限流检查失败（%s），本次放行", exc)
