"""后台 · C 端用户管理与封禁。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db, pagination_params
from app.core.permissions import USER_MANAGE, require_permission
from app.models import AdminUser, Essay, User, UserStatus
from app.schemas.common import Page, mask_phone
from app.schemas.user import UserOut
from app.services.audit_service import audit
from app.services.ws_manager import manager

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("", response_model=Page[UserOut])
async def list_users(
    keyword: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[UserOut]:
    page, page_size = pagination
    filters = []
    if keyword:
        pattern = f"%{keyword.strip()}%"
        filters.append(or_(User.nickname.ilike(pattern), User.phone.ilike(pattern)))

    total = await db.scalar(select(func.count()).select_from(User).where(*filters))
    rows = (
        await db.execute(
            select(User, func.count(Essay.id))
            .outerjoin(Essay, Essay.user_id == User.id)
            .where(*filters)
            .group_by(User.id)
            .order_by(User.created_at.desc(), User.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[
            UserOut(
                id=user.id,
                nickname=user.nickname,
                avatar=user.avatar,
                phone=mask_phone(user.phone),
                essay_count=int(count or 0),
                status=user.status,
                created_at=user.created_at,
                last_login_at=user.last_login_at,
            )
            for user, count in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


async def _set_user_status(
    db: AsyncSession,
    user_id: int,
    status: str,
    *,
    action: str,
    admin: AdminUser,
    request: Request,
) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.status = status
    await audit(
        db,
        action=action,
        resource_type="user",
        resource_id=user.id,
        detail={"nickname": user.nickname, "status": status},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    if status == UserStatus.BANNED.value:
        await manager.disconnect_user(user_id)
    await db.refresh(user)
    essay_count = await db.scalar(
        select(func.count()).select_from(Essay).where(Essay.user_id == user_id)
    )
    return UserOut(
        id=user.id,
        nickname=user.nickname,
        avatar=user.avatar,
        phone=mask_phone(user.phone),
        essay_count=int(essay_count or 0),
        status=user.status,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.post("/{user_id}/ban", response_model=UserOut)
async def ban_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(USER_MANAGE)),
) -> UserOut:
    # 封禁后：已签发的 C 端 token 在 get_current_user 处立即失效（401）
    return await _set_user_status(
        db, user_id, UserStatus.BANNED.value, action="user.ban", admin=admin, request=request
    )


@router.post("/{user_id}/unban", response_model=UserOut)
async def unban_user(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(USER_MANAGE)),
) -> UserOut:
    return await _set_user_status(
        db, user_id, UserStatus.ACTIVE.value, action="user.unban", admin=admin, request=request
    )
