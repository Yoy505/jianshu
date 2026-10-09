"""微信小程序登录：code2session + openid 自动注册。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import SCOPE_USER, create_token
from app.models import User, UserStatus
from app.schemas.user import CLoginResponse, WxLoginRequest
from app.services import generate_nickname, serialize_user
from app.services.wx_service import code2session

router = APIRouter(prefix="/wx", tags=["wx"])


@router.post("/login", response_model=CLoginResponse)
async def wx_login(body: WxLoginRequest, db: AsyncSession = Depends(get_db)) -> CLoginResponse:
    session_info = await code2session(body.code)
    openid = session_info.get("openid")
    unionid = session_info.get("unionid")

    user = (
        await db.execute(select(User).where(User.wx_openid == openid))
    ).scalar_one_or_none()

    is_new = False
    if user is None:
        # openid 自动注册：随机中文笔名 + 2 位数字
        user = User(
            nickname=generate_nickname(),
            wx_openid=openid,
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
        user.last_login_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)

    return CLoginResponse(
        token=create_token(user.id, SCOPE_USER, "access"),
        refresh_token=create_token(user.id, SCOPE_USER, "refresh"),
        user=await serialize_user(db, user),
        is_new=is_new,
    )
