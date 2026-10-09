"""H5 手机号验证码登录。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_redis
from app.core.security import SCOPE_USER, create_token
from app.models import User, UserStatus
from app.schemas.user import CLoginResponse, SmsLoginRequest, SmsSendRequest
from app.services import generate_nickname, serialize_user
from app.services.sms_service import issue_code, validate_phone, verify_code

router = APIRouter(prefix="/h5/auth", tags=["h5-auth"])


@router.post("/sms/send", status_code=202)
async def send_sms_code(
    body: SmsSendRequest,
    redis: Redis = Depends(get_redis),
) -> dict:
    validate_phone(body.phone)
    await issue_code(redis, body.phone)
    return {"message": "验证码已发送"}


@router.post("/sms/login", response_model=CLoginResponse)
async def sms_login(
    body: SmsLoginRequest,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> CLoginResponse:
    validate_phone(body.phone)
    await verify_code(redis, body.phone, body.code)

    user = (
        await db.execute(select(User).where(User.phone == body.phone))
    ).scalar_one_or_none()

    is_new = False
    if user is None:
        user = User(
            nickname=generate_nickname(),
            phone=body.phone,
            status=UserStatus.ACTIVE.value,
            last_login_at=datetime.now(timezone.utc),
        )
        db.add(user)
        is_new = True
    else:
        if user.status == UserStatus.BANNED.value:
            raise HTTPException(status_code=403, detail="账号已被封禁")
        user.last_login_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)

    return CLoginResponse(
        token=create_token(user.id, SCOPE_USER, "access"),
        refresh_token=create_token(user.id, SCOPE_USER, "refresh"),
        user=await serialize_user(db, user),
        is_new=is_new,
    )
