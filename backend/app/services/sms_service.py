"""短信验证码业务：手机号校验、60s 冷却、每日限额、验证码签发与校验。

Redis Key 约定（见 docs/api.md）：
- sms:{phone}:code      值 "code#尝试次数"，TTL 300s
- sms:{phone}:cooldown  发送冷却，TTL 60s
- sms:{phone}:daily     每日发送计数，TTL 至当日末
"""

import random
import re
from datetime import datetime, time, timedelta, timezone

from fastapi import HTTPException
from redis.asyncio import Redis

from app.core.config import settings
from app.workers.tasks import send_sms

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")  # 大陆手机号

DAILY_LIMIT = 5
CODE_TTL_SECONDS = 300
COOLDOWN_SECONDS = 60
MAX_VERIFY_ATTEMPTS = 5


def _require_sms_channel() -> None:
    if settings.APP_ENV not in {"dev", "test"}:
        raise HTTPException(status_code=503, detail="短信通道尚未接入，验证码无法发送或登录")


def validate_phone(phone: str) -> None:
    if not PHONE_RE.match(phone or ""):
        raise HTTPException(status_code=400, detail="手机号格式不正确")


def _seconds_until_day_end(now: datetime | None = None) -> int:
    """距 UTC 当日结束的秒数（用于每日计数 key 的过期时间）。"""
    now = now or datetime.now(timezone.utc)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return max(1, int((tomorrow - now).total_seconds()))


async def issue_code(redis: Redis, phone: str) -> str:
    """发送验证码：每日限额 → 60s 冷却 → 生成 6 位码并投递 Celery 任务。"""
    _require_sms_channel()
    daily_key = f"sms:{phone}:daily"
    daily = await redis.incr(daily_key)
    if daily == 1:
        await redis.expire(daily_key, _seconds_until_day_end())
    if daily > DAILY_LIMIT:
        raise HTTPException(status_code=429, detail="今日发送次数已达上限")

    cooldown_key = f"sms:{phone}:cooldown"
    acquired = await redis.set(cooldown_key, "1", ex=COOLDOWN_SECONDS, nx=True)
    if not acquired:
        raise HTTPException(status_code=429, detail="发送过于频繁，请稍后再试")

    code = f"{random.randint(0, 999999):06d}"
    await redis.set(f"sms:{phone}:code", f"{code}#0", ex=CODE_TTL_SECONDS)
    send_sms.delay(phone, code)
    return code


async def verify_code(redis: Redis, phone: str, code: str) -> None:
    """校验验证码：错误累加尝试次数，达到 5 次作废；通过后立即删除。"""
    _require_sms_channel()
    code_key = f"sms:{phone}:code"
    raw = await redis.get(code_key)
    if not raw or "#" not in raw:
        raise HTTPException(status_code=400, detail="验证码已失效")

    stored_code, _, attempts_raw = raw.partition("#")
    try:
        attempts = int(attempts_raw)
    except ValueError:
        attempts = 0

    if attempts >= MAX_VERIFY_ATTEMPTS:
        await redis.delete(code_key)
        raise HTTPException(status_code=400, detail="验证码已失效")

    if stored_code != (code or ""):
        # 保留剩余 TTL，累加尝试次数
        ttl = await redis.ttl(code_key)
        await redis.set(code_key, f"{stored_code}#{attempts + 1}", ex=ttl if ttl > 0 else CODE_TTL_SECONDS)
        raise HTTPException(status_code=400, detail="验证码错误")

    await redis.delete(code_key)
