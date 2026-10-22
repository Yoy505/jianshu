"""微信小程序登录：微信身份与手机号双凭据登录。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db, get_redis
from app.core.security import SCOPE_USER, create_token
from app.models import User, UserStatus
from app.schemas.user import CLoginResponse, WxLoginRequest
from app.services import generate_nickname, serialize_user
from app.services.wx_service import code2session, phone_number

router = APIRouter(prefix="/wx", tags=["wx"])


@router.post("/login", response_model=CLoginResponse)
async def wx_login(
    body: WxLoginRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> CLoginResponse:
    # 仅保留既有本地 dev-code 测试入口；真实微信登录一律要求手机号授权。
    dev_login = settings.APP_ENV in {"dev", "test"} and body.code == "dev-code" and not settings.WX_APPID and not settings.WX_SECRET
    if not body.phone_code and not dev_login:
        raise HTTPException(status_code=400, detail="请使用微信手机号快捷登录")
    session_info = await code2session(body.code)
    openid = session_info.get("openid")
    unionid = session_info.get("unionid")

    phone = await phone_number(body.phone_code, redis) if body.phone_code else None

    user = (
        await db.execute(select(User).where(User.wx_openid == openid).with_for_update())
    ).scalar_one_or_none()

    phone_user = None
    if phone:
        phone_user = (await db.execute(select(User).where(User.phone == phone).with_for_update())).scalar_one_or_none()
    for account in (user, phone_user):
        if account and account.status == UserStatus.BANNED.value:
            raise HTTPException(status_code=403, detail="账号已被封禁")
    if (user and phone_user and user.id != phone_user.id) or (
        phone_user and phone_user.wx_openid and phone_user.wx_openid != openid
    ) or (user and user.phone and user.phone != phone):
        raise HTTPException(status_code=409, detail="手机号与已有账号绑定不一致，请联系客服处理")
    if user is None and phone_user is not None:
        user = phone_user
        user.wx_openid = openid
    is_new = False
    if user is None:
        # openid 自动注册：随机中文笔名 + 2 位数字
        user = User(
            nickname=generate_nickname(),
            wx_openid=openid,
            phone=phone,
            wx_unionid=unionid,
            status=UserStatus.ACTIVE.value,
            last_login_at=datetime.now(timezone.utc),
        )
        db.add(user)
        is_new = True
    else:
        if user.status == UserStatus.BANNED.value:
            raise HTTPException(status_code=403, detail="账号已被封禁")
        if unionid and not user.wx_unionid:
            user.wx_unionid = unionid
        if phone:
            user.phone = phone
        user.last_login_at = datetime.now(timezone.utc)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="账号绑定状态已变化，请重新登录")
    await db.refresh(user)

    return CLoginResponse(
        token=create_token(user.id, SCOPE_USER, "access"),
        refresh_token=create_token(user.id, SCOPE_USER, "refresh"),
        user=await serialize_user(db, user),
        is_new=is_new,
    )
