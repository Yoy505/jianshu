"""后台 · 征文作品审核与管理。"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, join, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db, pagination_params
from app.core.permissions import (
    ESSAY_EXPORT,
    ESSAY_FEATURE,
    ESSAY_OFFLINE,
    ESSAY_REVIEW,
    require_permission,
)
from app.models import AdminUser, Contest, Essay, EssayReview, EssayStatus, User
from app.schemas.admin import ExportAccepted, ExportRequest, FeatureRequest, ReviewRequest
from app.services.audit_service import audit
from app.services.export_storage import private_export_dir
from app.schemas.common import Page
from app.schemas.essay import (
    EssayAdminDetail,
    EssayListItem,
    EssayReviewOut,
    essay_common_fields,
)
from app.workers.tasks import export_essays_xlsx

router = APIRouter(prefix="/admin/essays", tags=["admin-essays"])

_VALID_STATUS = {status.value for status in EssayStatus}
_VALID_SORT_COLUMNS = {"created_at": Essay.created_at, "like_count": Essay.like_count, "view_count": Essay.view_count}
_EXPORT_FILENAME = re.compile(r"essays_\d{8}_\d{6}_[0-9a-f]{32}\.xlsx\Z")


async def _load_full_row(db: AsyncSession, essay_id: int):
    return (
        await db.execute(
            select(Essay, User, Contest)
            .join(User, Essay.user_id == User.id)
            .join(Contest, Essay.contest_id == Contest.id)
            .where(Essay.id == essay_id)
        )
    ).first()


async def _admin_detail(db: AsyncSession, essay_id: int) -> EssayAdminDetail:
    row = await _load_full_row(db, essay_id)
    if row is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    essay, user, contest = row
    review_rows = (
        await db.execute(
            select(EssayReview, AdminUser.name)
            .join(AdminUser, EssayReview.admin_id == AdminUser.id)
            .where(EssayReview.essay_id == essay_id)
            .order_by(EssayReview.id.asc())
        )
    ).all()
    return EssayAdminDetail(
        **essay_common_fields(essay, user, contest),
        content=essay.content,
        liked_by_me=False,
        reviews=[
            EssayReviewOut(
                id=review.id,
                admin_name=admin_name,
                action=review.action,
                score=review.score,
                comment=review.comment,
                created_at=review.created_at,
            )
            for review, admin_name in review_rows
        ],
    )


@router.get("", response_model=Page[EssayListItem])
async def list_essays(
    status: str | None = Query(default=None),
    contest_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[EssayListItem]:
    page, page_size = pagination
    if status is not None and status not in _VALID_STATUS:
        raise HTTPException(status_code=400, detail="无效的作品状态")
    if sort_by not in _VALID_SORT_COLUMNS:
        raise HTTPException(status_code=400, detail="无效的排序字段")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="无效的排序方向")
    sort_column = _VALID_SORT_COLUMNS[sort_by]

    filters = []
    if status:
        filters.append(Essay.status == status)
    if contest_id is not None:
        filters.append(Essay.contest_id == contest_id)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        filters.append(or_(Essay.title.ilike(pattern), User.nickname.ilike(pattern)))

    total = await db.scalar(
        select(func.count())
        .select_from(join(Essay, User, Essay.user_id == User.id))
        .where(*filters)
    )
    rows = (
        await db.execute(
            select(Essay, User, Contest)
            .join(User, Essay.user_id == User.id)
            .join(Contest, Essay.contest_id == Contest.id)
            .where(*filters)
            .order_by(sort_column.desc() if order == "desc" else sort_column.asc(), Essay.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[EssayListItem(**essay_common_fields(essay, user, contest)) for essay, user, contest in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.post("/export", response_model=ExportAccepted, status_code=202)
async def export_essays(
    body: ExportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(ESSAY_EXPORT)),
) -> ExportAccepted:
    task = export_essays_xlsx.delay(body.contest_id, body.status)
    await audit(
        db,
        action="essay.export",
        resource_type="essay",
        resource_id=None,
        detail={"task_id": task.id, "contest_id": body.contest_id, "status": body.status},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    return ExportAccepted(task_id=task.id)


@router.get("/exports/{filename}")
async def download_essay_export(
    filename: str,
    admin: AdminUser = Depends(require_permission(ESSAY_EXPORT)),
) -> FileResponse:
    if not _EXPORT_FILENAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="导出文件不存在")
    directory = private_export_dir()
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return FileResponse(
        path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{essay_id}", response_model=EssayAdminDetail)
async def get_essay(
    essay_id: int,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> EssayAdminDetail:
    return await _admin_detail(db, essay_id)


@router.post("/{essay_id}/review", response_model=EssayAdminDetail)
async def review_essay(
    essay_id: int,
    body: ReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(ESSAY_REVIEW)),
) -> EssayAdminDetail:
    essay = await db.get(Essay, essay_id)
    if essay is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    previous = essay.status
    if body.action == "approve":
        essay.status = EssayStatus.APPROVED.value
        if body.score is not None:
            essay.score = body.score
    else:
        essay.status = EssayStatus.REJECTED.value
    db.add(
        EssayReview(
            essay_id=essay.id,
            admin_id=admin.id,
            action=body.action,
            score=body.score,
            comment=body.comment,
        )
    )
    await audit(
        db,
        action="essay.review",
        resource_type="essay",
        resource_id=essay.id,
        detail={"from": previous, "to": essay.status, "score": body.score},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    return await _admin_detail(db, essay_id)


@router.post("/{essay_id}/feature", response_model=EssayAdminDetail)
async def feature_essay(
    essay_id: int,
    body: FeatureRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(ESSAY_FEATURE)),
) -> EssayAdminDetail:
    essay = await db.get(Essay, essay_id)
    if essay is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    essay.featured = body.featured
    await audit(
        db,
        action="essay.feature",
        resource_type="essay",
        resource_id=essay.id,
        detail={"featured": body.featured},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    return await _admin_detail(db, essay_id)


@router.post("/{essay_id}/offline", response_model=EssayAdminDetail)
async def offline_essay(
    essay_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(ESSAY_OFFLINE)),
) -> EssayAdminDetail:
    essay = await db.get(Essay, essay_id)
    if essay is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    # 仅已通过审核的作品可下架
    if essay.status != EssayStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="仅已通过审核的作品可下架")
    essay.status = EssayStatus.OFFLINE.value
    db.add(
        EssayReview(
            essay_id=essay.id,
            admin_id=admin.id,
            action="offline",
            score=None,
            comment=None,
        )
    )
    await audit(
        db,
        action="essay.offline",
        resource_type="essay",
        resource_id=essay.id,
        detail={"from": EssayStatus.APPROVED.value, "to": EssayStatus.OFFLINE.value},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    return await _admin_detail(db, essay_id)
