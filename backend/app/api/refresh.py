"""通用刷新端点：按 refresh token 的 scope 重签 admin/user 各自的 token 对。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import (
    SCOPE_ADMIN,
    SCOPE_ORG,
    SCOPE_USER,
    TOKEN_TYPE_REFRESH,
    create_token,
    decode_token,
)
from app.models import AdminUser, MpOrg, MpOrgAccount, User, UserStatus
from app.schemas.auth import RefreshAdminResponse, RefreshRequest, RefreshUserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/refresh")
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if payload.get("type") != TOKEN_TYPE_REFRESH:
        raise HTTPException(status_code=401, detail="无效的刷新令牌")
    try:
        subject_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="无效的刷新令牌")

    scope = payload.get("scope")
    if scope == SCOPE_ADMIN:
        admin = await db.get(AdminUser, subject_id)
        if admin is None:
            raise HTTPException(status_code=401, detail="账号不存在")
        return RefreshAdminResponse(
            access_token=create_token(admin.id, SCOPE_ADMIN, "access"),
            refresh_token=create_token(admin.id, SCOPE_ADMIN, "refresh"),
        )
    if scope == SCOPE_USER:
        user = await db.get(User, subject_id)
        if user is None:
            raise HTTPException(status_code=401, detail="账号不存在")
        if user.status == UserStatus.BANNED.value:
            raise HTTPException(status_code=401, detail="账号已被封禁")
        return RefreshUserResponse(
            token=create_token(user.id, SCOPE_USER, "access"),
            refresh_token=create_token(user.id, SCOPE_USER, "refresh"),
        )
    if scope == SCOPE_ORG:
        account = await db.get(MpOrgAccount, subject_id)
        if account is None or account.status != "active":
            raise HTTPException(status_code=401, detail="账号不存在或已停用")
        org = await db.get(MpOrg, account.org_id)
        if org is None or org.status != "active":
            raise HTTPException(status_code=401, detail="单位已停用")
        return RefreshAdminResponse(
            access_token=create_token(account.id, SCOPE_ORG, "access"),
            refresh_token=create_token(account.id, SCOPE_ORG, "refresh"),
        )
    raise HTTPException(status_code=401, detail="无效的刷新令牌")
