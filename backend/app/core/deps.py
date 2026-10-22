"""FastAPI 依赖项：数据库会话、Redis、认证（admin/user/任意有效 token）、分页。"""

from typing import AsyncIterator

from fastapi import Depends, Header, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import SCOPE_ADMIN, SCOPE_ORG, SCOPE_USER, TOKEN_TYPE_ACCESS, decode_token
from app.db.session import AsyncSessionLocal
from app.models import AdminUser, MpOrg, MpOrgAccount, User, UserStatus

_redis_client: Redis | None = None


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


def _parse_bearer(authorization: str | None) -> dict:
    """解析 Authorization: Bearer <token>，失败统一 401。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    return decode_token(authorization.split(" ", 1)[1].strip())


def _sub_id(payload: dict) -> int:
    try:
        return int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="无效的登录凭证")


def _require_access_token(payload: dict) -> None:
    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise HTTPException(status_code=401, detail="令牌类型或权限不正确")


async def get_current_admin(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    payload = _parse_bearer(authorization)
    _require_access_token(payload)
    if payload.get("scope") != SCOPE_ADMIN:
        raise HTTPException(status_code=401, detail="令牌类型或权限不正确")
    admin = await db.get(AdminUser, _sub_id(payload))
    if admin is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    return admin


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = _parse_bearer(authorization)
    _require_access_token(payload)
    if payload.get("scope") != SCOPE_USER:
        raise HTTPException(status_code=401, detail="令牌类型或权限不正确")
    user = await db.get(User, _sub_id(payload))
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    # 封禁立即生效：已签发的 token 在此被拒绝
    if user.status == UserStatus.BANNED.value:
        raise HTTPException(status_code=401, detail="账号已被封禁")
    return user


async def get_current_user_optional(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """匿名可访问接口使用：无 Authorization 头时返回 None，携带则严格校验。"""
    if authorization is None:
        return None
    return await get_current_user(authorization=authorization, db=db)


async def get_current_org_account(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> tuple[MpOrgAccount, MpOrg]:
    """采购单位查看账号（scope=org）：账号与单位任一停用即 401。"""
    payload = _parse_bearer(authorization)
    _require_access_token(payload)
    if payload.get("scope") != SCOPE_ORG:
        raise HTTPException(status_code=401, detail="令牌类型或权限不正确")
    account = await db.get(MpOrgAccount, _sub_id(payload))
    if account is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    if account.status != "active":
        raise HTTPException(status_code=401, detail="账号已停用")
    org = await db.get(MpOrg, account.org_id)
    if org is None or org.status != "active":
        raise HTTPException(status_code=401, detail="单位已停用")
    return account, org


async def get_current_principal(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """上传等接口使用：admin 或 user 任一有效 token 均可，返回 (scope, 账号)。"""
    payload = _parse_bearer(authorization)
    _require_access_token(payload)
    scope = payload.get("scope")
    if scope == SCOPE_ADMIN:
        admin = await db.get(AdminUser, _sub_id(payload))
        if admin is None:
            raise HTTPException(status_code=401, detail="账号不存在")
        return SCOPE_ADMIN, admin
    if scope == SCOPE_USER:
        user = await db.get(User, _sub_id(payload))
        if user is None:
            raise HTTPException(status_code=401, detail="账号不存在")
        if user.status == UserStatus.BANNED.value:
            raise HTTPException(status_code=401, detail="账号已被封禁")
        return SCOPE_USER, user
    raise HTTPException(status_code=401, detail="令牌类型或权限不正确")


async def pagination_params(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> tuple[int, int]:
    return page, page_size
