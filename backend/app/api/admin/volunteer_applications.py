"""志愿者报名后台：admin 专属查看与状态管理（契约 §七之三）。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, pagination_params
from app.core.permissions import VOLUNTEER_READ, require_permission
from app.models import AdminUser, VolunteerApplication
from app.schemas.common import Page
from app.schemas.volunteer import (
    volunteer_detail,
    volunteer_list_item,
    VolunteerDetail,
    VolunteerListItem,
    VolunteerStatusRequest,
)
from app.services.audit_service import audit

router = APIRouter(prefix="/admin/volunteer-applications", tags=["admin-volunteer-applications"])
STATUSES = {"pending", "contacted", "enrolled"}


@router.get("", response_model=Page[VolunteerListItem])
async def list_applications(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(VOLUNTEER_READ)),
) -> Page[VolunteerListItem]:
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=400, detail="无效的报名状态")
    filters = []
    if status:
        filters.append(VolunteerApplication.status == status)
    if q and q.strip():
        term = f"%{q.strip()[:50]}%"
        filters.append(or_(
            VolunteerApplication.name.ilike(term),
            VolunteerApplication.phone.ilike(term),
            VolunteerApplication.city.ilike(term),
            VolunteerApplication.organization.ilike(term),
            VolunteerApplication.receipt_no.ilike(term),
        ))
    page, page_size = pagination
    total = await db.scalar(
        select(func.count()).select_from(VolunteerApplication).where(*filters)
    )
    rows = (
        await db.execute(
            select(VolunteerApplication)
            .where(*filters)
            .order_by(VolunteerApplication.created_at.desc(), VolunteerApplication.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[volunteer_list_item(row) for row in rows],
        total=int(total or 0), page=page, page_size=page_size,
    )


@router.get("/{application_id}", response_model=VolunteerDetail)
async def get_application(
    application_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(VOLUNTEER_READ)),
) -> VolunteerDetail:
    row = await db.get(VolunteerApplication, application_id)
    if row is None:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    await audit(
        db, action="volunteer.detail_read", resource_type="volunteer_application",
        resource_id=row.id, actor_id=admin.id, actor_name=admin.name, request=request,
    )
    await db.commit()
    return volunteer_detail(row)


@router.post("/{application_id}/status", response_model=VolunteerDetail)
async def update_status(
    application_id: int,
    body: VolunteerStatusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(VOLUNTEER_READ)),
) -> VolunteerDetail:
    row = await db.get(VolunteerApplication, application_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="报名记录不存在")
    previous = row.status
    row.status = body.status
    await audit(
        db, action="volunteer.status", resource_type="volunteer_application",
        resource_id=row.id, detail={"from": previous, "to": row.status},
        actor_id=admin.id, actor_name=admin.name, request=request,
    )
    await db.commit()
    await db.refresh(row)
    return volunteer_detail(row)
