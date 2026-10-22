"""杏临千行志愿者公开报名：无账号依赖，限流失败关闭。"""

import hashlib
import hmac
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import field_validator
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db, get_redis
from app.models import VolunteerApplication
from app.schemas.volunteer import (
    CONSENT_TEXT,
    TRACKS,
    VolunteerApplicationRequest,
    VolunteerReceipt,
)

router = APIRouter(prefix="/h5/volunteer-applications", tags=["h5-volunteer-applications"])
MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in {"127.0.0.1", "::1"}:
        forwarded = request.headers.get("x-real-ip")
        if forwarded:
            return forwarded[:64]
    return peer


async def _limit(redis: Redis, key: str, maximum: int, window_seconds: int) -> None:
    try:
        count = await redis.incr(key)
        if count == 1:
            if not await redis.expire(key, window_seconds):
                raise RuntimeError("限流窗口未设置成功")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="报名服务暂不可用，请稍后再试") from exc
    if count > maximum:
        raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")


@router.get("/meta")
async def volunteer_meta() -> dict:
    return {
        "enabled": settings.VOLUNTEER_APPLICATIONS_ENABLED,
        "tracks": [{"key": key, "label": label} for key, label in TRACKS],
        "consent_text": CONSENT_TEXT,
    }


class VolunteerCreateRequest(VolunteerApplicationRequest):
    """提交侧补充手机号格式校验（大陆手机号）。"""

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        phone = value.strip()
        if not MOBILE_RE.fullmatch(phone):
            raise ValueError("联系电话需为大陆手机号")
        return phone

    @field_validator("name", "city", "occupation", "organization")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("intro")
    @classmethod
    def strip_intro(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


@router.post("", response_model=VolunteerReceipt, status_code=201)
async def create_volunteer_application(
    body: VolunteerCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> VolunteerReceipt:
    if not settings.VOLUNTEER_APPLICATIONS_ENABLED:
        raise HTTPException(status_code=503, detail="志愿者报名暂未开放")
    if body.consent is not True:
        raise HTTPException(status_code=422, detail="请先确认志愿者报名信息使用说明")
    if not body.name or not body.city:
        raise HTTPException(status_code=422, detail="姓名与所在城市为必填项")
    track_note = (body.track_note or "").strip()
    if body.track == "other" and not track_note:
        raise HTTPException(status_code=422, detail="选择「其他」方向时请填写具体服务方向")

    ip = _client_ip(request)
    phone_digest = hmac.new(
        settings.JWT_SECRET.encode(), body.phone.encode(), hashlib.sha256
    ).hexdigest()[:24]
    await _limit(redis, f"volunteer:apply:ip:{ip}", 10, 3600)
    await _limit(redis, f"volunteer:apply:phone:{phone_digest}", 3, 86400)

    row = VolunteerApplication(
        receipt_no=f"VOL-{uuid.uuid4().hex[:16].upper()}",
        name=body.name,
        phone=body.phone,
        city=body.city,
        birth_year=body.birth_year,
        occupation=body.occupation,
        organization=body.organization,
        track=body.track,
        track_note=track_note if body.track == "other" else None,
        offline_per_year=body.offline_per_year,
        can_station=body.can_station,
        intro=body.intro,
        status="pending",
        consent=True,
        consent_text=CONSENT_TEXT,
        consented_at=datetime.now(timezone.utc),
        contact_ip=ip,
    )
    try:
        db.add(row)
        await db.flush()
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(row)
    return VolunteerReceipt(id=row.id, receipt_no=row.receipt_no, submitted_at=row.created_at)
