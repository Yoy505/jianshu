"""职业健康作品后台：两级评审（方案 B）、最小权限读取、私有下载。"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, pagination_params
from app.core.permissions import (
    OCCUPATIONAL_FINAL_REVIEW,
    OCCUPATIONAL_INITIAL_REVIEW,
    OCCUPATIONAL_READ,
    require_permission,
)
from app.models import AdminUser, OccupationalReviewRecord, OccupationalSubmission
from app.schemas.common import Page
from app.schemas.occupational import (
    OccupationalDetail,
    OccupationalFinalReviewRequest,
    OccupationalInitialReviewRequest,
    OccupationalListItem,
    OccupationalLockInfo,
    OccupationalReviewRecordOut,
    REVIEW_STATUSES,
    submission_detail,
    submission_list_item,
)
from app.services.audit_service import audit
from app.services.occupational_submission_service import private_file_path

router = APIRouter(prefix="/admin/occupational-submissions", tags=["admin-occupational-submissions"])
WORK_TYPES = {"article", "video"}
INITIAL_LOCK_TTL = timedelta(minutes=30)


def _utc(value: datetime) -> datetime:
    """SQLite 可能返回 naive datetime；统一按 UTC 补时区后再比较。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _lock_expired(row: OccupationalSubmission) -> bool:
    return row.locked_at is None or datetime.now(timezone.utc) - _utc(row.locked_at) > INITIAL_LOCK_TTL


async def _detail_with_reviews(db: AsyncSession, row: OccupationalSubmission) -> OccupationalDetail:
    records = (
        (
            await db.execute(
                select(OccupationalReviewRecord)
                .where(OccupationalReviewRecord.submission_id == row.id)
                .order_by(OccupationalReviewRecord.id.asc())
            )
        )
        .scalars()
        .all()
    )
    locked_name = None
    if row.locked_by_admin_id is not None:
        locked_name = await db.get(AdminUser, row.locked_by_admin_id)
        locked_name = locked_name.name if locked_name else None
    lock = OccupationalLockInfo(
        locked_by_admin_id=row.locked_by_admin_id,
        locked_by_name=locked_name,
        locked_at=row.locked_at,
        expired=_lock_expired(row),
    )
    return submission_detail(
        row,
        reviews=[
            OccupationalReviewRecordOut(
                stage=record.stage,
                reviewer_id=record.reviewer_id,
                reviewer_name=record.reviewer_name,
                conclusion=record.conclusion,
                comment=record.comment,
                score=record.score,
                created_at=record.created_at,
            )
            for record in records
        ],
        lock=lock,
    )


@router.get("", response_model=Page[OccupationalListItem])
async def list_submissions(
    status: str | None = Query(default=None),
    work_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(OCCUPATIONAL_READ)),
) -> Page[OccupationalListItem]:
    if status is not None and status not in REVIEW_STATUSES:
        raise HTTPException(status_code=400, detail="无效的审核状态")
    if work_type is not None and work_type not in WORK_TYPES:
        raise HTTPException(status_code=400, detail="无效的作品类型")
    filters = []
    if status:
        filters.append(OccupationalSubmission.status == status)
    if work_type:
        filters.append(OccupationalSubmission.work_type == work_type)
    if q and q.strip():
        term = f"%{q.strip()[:100]}%"
        filters.append(or_(
            OccupationalSubmission.title.ilike(term),
            OccupationalSubmission.author_name.ilike(term),
            OccupationalSubmission.organization.ilike(term),
            OccupationalSubmission.receipt_no.ilike(term),
        ))
    page, page_size = pagination
    total = await db.scalar(select(func.count()).select_from(OccupationalSubmission).where(*filters))
    rows = (
        await db.execute(
            select(OccupationalSubmission)
            .where(*filters)
            .order_by(OccupationalSubmission.created_at.desc(), OccupationalSubmission.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[submission_list_item(row) for row in rows],
        total=int(total or 0), page=page, page_size=page_size,
    )


@router.get("/{submission_id}", response_model=OccupationalDetail)
async def get_submission(
    submission_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(OCCUPATIONAL_READ)),
) -> OccupationalDetail:
    row = await db.get(OccupationalSubmission, submission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="投稿不存在")
    await audit(
        db, action="occupational.detail_read", resource_type="occupational_submission",
        resource_id=row.id, actor_id=admin.id, actor_name=admin.name, request=request,
    )
    await db.commit()
    return await _detail_with_reviews(db, row)


@router.get("/{submission_id}/file")
async def download_submission(
    submission_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(OCCUPATIONAL_READ)),
) -> FileResponse:
    row = await db.get(OccupationalSubmission, submission_id)
    if row is None:
        raise HTTPException(status_code=404, detail="投稿不存在")
    path = private_file_path(row.stored_filename)
    await audit(
        db, action="occupational.file_download", resource_type="occupational_submission",
        resource_id=row.id, actor_id=admin.id, actor_name=admin.name, request=request,
    )
    await db.commit()
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if row.work_type == "article" else "video/mp4"
    )
    return FileResponse(
        path, filename=row.original_filename, media_type=media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _assert_lock_usable(row: OccupationalSubmission, admin: AdminUser) -> None:
    """pass/reject 前置：持锁人可直接操作；锁过期允许接管；他人有效锁拒绝。"""
    if row.locked_by_admin_id == admin.id:
        return
    if not _lock_expired(row):
        raise HTTPException(status_code=409, detail="该稿件正由其他初审员处理，暂不能操作")


@router.post("/{submission_id}/initial-review", response_model=OccupationalDetail)
async def initial_review(
    submission_id: int,
    body: OccupationalInitialReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(OCCUPATIONAL_INITIAL_REVIEW)),
) -> OccupationalDetail:
    row = await db.get(OccupationalSubmission, submission_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="投稿不存在")
    comment = (body.comment or "").strip()
    now = datetime.now(timezone.utc)
    previous = row.status

    if body.action == "claim":
        if row.status not in {"submitted", "initial_reviewing"}:
            raise HTTPException(status_code=409, detail="当前状态不可领取初审")
        if row.status == "initial_reviewing":
            _assert_lock_usable(row, admin)
        row.status = "initial_reviewing"
        row.locked_by_admin_id = admin.id
        row.locked_at = now
    elif body.action == "release":
        if row.status != "initial_reviewing":
            raise HTTPException(status_code=409, detail="仅初审中的稿件可释放")
        if row.locked_by_admin_id != admin.id:
            raise HTTPException(status_code=409, detail="只有持锁人可释放初审")
        row.status = "submitted"
        row.locked_by_admin_id = None
        row.locked_at = None
    elif body.action == "pass":
        if row.status != "initial_reviewing":
            raise HTTPException(status_code=409, detail="初审通过前须先领取稿件")
        _assert_lock_usable(row, admin)
        row.status = "pending_final"
        row.locked_by_admin_id = None
        row.locked_at = None
        row.review_comment = comment or None
        row.reviewed_by = admin.id
        row.reviewed_at = now
        db.add(OccupationalReviewRecord(
            submission_id=row.id, stage="initial", reviewer_id=admin.id,
            reviewer_name=admin.name, conclusion="passed", comment=comment or None, score=None,
        ))
    else:  # reject
        if not comment:
            raise HTTPException(status_code=422, detail="驳回时须填写原因")
        if row.status != "initial_reviewing":
            raise HTTPException(status_code=409, detail="初审驳回前须先领取稿件")
        _assert_lock_usable(row, admin)
        row.status = "rejected"
        row.locked_by_admin_id = None
        row.locked_at = None
        row.review_comment = comment
        row.reviewed_by = admin.id
        row.reviewed_at = now
        db.add(OccupationalReviewRecord(
            submission_id=row.id, stage="initial", reviewer_id=admin.id,
            reviewer_name=admin.name, conclusion="rejected", comment=comment, score=None,
        ))

    await audit(
        db, action="occupational.initial_review", resource_type="occupational_submission",
        resource_id=row.id,
        detail={"action": body.action, "from": previous, "to": row.status},
        actor_id=admin.id, actor_name=admin.name, request=request,
    )
    await db.commit()
    await db.refresh(row)
    return await _detail_with_reviews(db, row)


@router.post("/{submission_id}/final-review", response_model=OccupationalDetail)
async def final_review(
    submission_id: int,
    body: OccupationalFinalReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(OCCUPATIONAL_FINAL_REVIEW)),
) -> OccupationalDetail:
    row = await db.get(OccupationalSubmission, submission_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="投稿不存在")
    if row.status != "pending_final":
        raise HTTPException(status_code=409, detail="仅初审通过待终审的稿件可终审")
    initial_record = (
        await db.execute(
            select(OccupationalReviewRecord).where(
                OccupationalReviewRecord.submission_id == row.id,
                OccupationalReviewRecord.stage == "initial",
            )
        )
    ).scalar_one_or_none()
    if initial_record is None:
        raise HTTPException(status_code=409, detail="缺少初审记录，不能终审")
    if initial_record.reviewer_id == admin.id:
        raise HTTPException(status_code=409, detail="同一账号不能既初审又终审同一稿件")

    comment = (body.comment or "").strip()
    if body.action == "reject" and not comment:
        raise HTTPException(status_code=422, detail="驳回时须填写原因")

    conclusion = "passed" if body.action == "pass" else "rejected"
    row.status = "accepted" if body.action == "pass" else "rejected"
    row.review_comment = comment or None
    row.reviewed_by = admin.id
    row.reviewed_at = datetime.now(timezone.utc)
    db.add(OccupationalReviewRecord(
        submission_id=row.id, stage="final", reviewer_id=admin.id,
        reviewer_name=admin.name, conclusion=conclusion,
        comment=comment or None,
        score=body.score if body.action == "pass" else None,
    ))
    await audit(
        db, action="occupational.final_review", resource_type="occupational_submission",
        resource_id=row.id,
        detail={"action": body.action, "to": row.status, "score": body.score},
        actor_id=admin.id, actor_name=admin.name, request=request,
    )
    await db.commit()
    await db.refresh(row)
    return await _detail_with_reviews(db, row)
