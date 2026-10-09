"""后台认证：登录 / 当前账号。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db
from app.core.security import SCOPE_ADMIN, create_token, verify_password
from app.models import AdminUser
from app.schemas.auth import AdminLoginRequest, AdminLoginResponse, AdminProfile
from app.services.audit_service import audit

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


@router.post("/login", response_model=AdminLoginResponse)
async def login(
    body: AdminLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AdminLoginResponse:
    admin = (
        await db.execute(select(AdminUser).where(AdminUser.username == body.username))
    ).scalar_one_or_none()
    if admin is None or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    await audit(
        db,
        action="admin.login",
        resource_type="admin_user",
        resource_id=admin.id,
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    return AdminLoginResponse(
        access_token=create_token(admin.id, SCOPE_ADMIN, "access"),
        refresh_token=create_token(admin.id, SCOPE_ADMIN, "refresh"),
        admin=AdminProfile(
            id=admin.id, username=admin.username, name=admin.name, role=admin.role
        ),
    )


@router.get("/me", response_model=AdminProfile)
async def me(admin: AdminUser = Depends(get_current_admin)) -> AdminProfile:
    return AdminProfile(id=admin.id, username=admin.username, name=admin.name, role=admin.role)
