"""后台 · 审计日志查询（只读；写入点见各命令端点内的 audit_service.audit 调用）。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db, pagination_params
from app.core.permissions import AUDIT_READ, require_permission
from app.models import AdminUser, AuditLog
from app.schemas.admin import AuditLogOut
from app.schemas.common import Page

router = APIRouter(prefix="/admin/audit-logs", tags=["admin-audit"])


@router.get("", response_model=Page[AuditLogOut])
async def list_audit_logs(
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(AUDIT_READ)),
) -> Page[AuditLogOut]:
    page, page_size = pagination
    filters = []
    if action:
        filters.append(AuditLog.action == action.strip())
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type.strip())

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    rows = (
        await db.execute(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[
            AuditLogOut(
                id=row.id,
                actor_type=row.actor_type,
                actor_id=row.actor_id,
                actor_name=row.actor_name,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                detail=row.detail,
                ip_address=row.ip_address,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )
