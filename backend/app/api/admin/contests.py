"""后台 · 征文活动管理。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db, pagination_params
from app.core.permissions import CONTEST_DELETE, CONTEST_MANAGE, require_permission
from app.models import AdminUser, Contest, ContestStatus, Essay
from app.schemas.common import Page, as_utc
from app.schemas.contest import ContestCreate, ContestOut, ContestStatusUpdate, ContestUpdate
from app.services.audit_service import audit

router = APIRouter(prefix="/admin/contests", tags=["admin-contests"])

_VALID_STATUS = {status.value for status in ContestStatus}


def _contest_out(contest: Contest, essay_count: int) -> ContestOut:
    return ContestOut(
        id=contest.id,
        title=contest.title,
        description=contest.description,
        cover_url=contest.cover_url,
        rules=contest.rules,
        prizes=contest.prizes,
        status=contest.status,
        start_at=contest.start_at,
        end_at=contest.end_at,
        essay_count=int(essay_count or 0),
        created_at=contest.created_at,
        updated_at=contest.updated_at,
    )


async def _load_contest_with_count(db: AsyncSession, contest_id: int):
    row = (
        await db.execute(
            select(Contest, func.count(Essay.id))
            .outerjoin(Essay, Essay.contest_id == Contest.id)
            .where(Contest.id == contest_id)
            .group_by(Contest.id)
        )
    ).first()
    return row


@router.get("", response_model=Page[ContestOut])
async def list_contests(
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[ContestOut]:
    page, page_size = pagination
    if status is not None and status not in _VALID_STATUS:
        raise HTTPException(status_code=400, detail="无效的活动状态")

    filters = []
    if keyword:
        filters.append(Contest.title.ilike(f"%{keyword.strip()}%"))
    if status:
        filters.append(Contest.status == status)

    total = await db.scalar(select(func.count()).select_from(Contest).where(*filters))
    rows = (
        await db.execute(
            select(Contest, func.count(Essay.id))
            .outerjoin(Essay, Essay.contest_id == Contest.id)
            .where(*filters)
            .group_by(Contest.id)
            .order_by(Contest.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[_contest_out(contest, count) for contest, count in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ContestOut, status_code=201)
async def create_contest(
    body: ContestCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(CONTEST_MANAGE)),
) -> ContestOut:
    if as_utc(body.start_at) >= as_utc(body.end_at):
        raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")
    contest = Contest(**body.model_dump())
    db.add(contest)
    await db.flush()  # 先拿到自增 id，保证审计与业务同事务
    await audit(
        db,
        action="contest.create",
        resource_type="contest",
        resource_id=contest.id,
        detail={"title": contest.title},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(contest)
    return _contest_out(contest, 0)


@router.get("/{contest_id}", response_model=ContestOut)
async def get_contest(
    contest_id: int,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ContestOut:
    row = await _load_contest_with_count(db, contest_id)
    if row is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    return _contest_out(row[0], row[1])


@router.patch("/{contest_id}", response_model=ContestOut)
async def update_contest(
    contest_id: int,
    body: ContestUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(CONTEST_MANAGE)),
) -> ContestOut:
    contest = await db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    data = body.model_dump(exclude_unset=True)
    if data:
        start = data.get("start_at", contest.start_at)
        end = data.get("end_at", contest.end_at)
        if as_utc(start) >= as_utc(end):
            raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")
        # 审计只记变更要点；长文本字段只记「已更新」标记
        audit_detail = {
            field: ("(updated)" if field in {"description", "rules", "prizes"} else value)
            for field, value in data.items()
        }
        for field, value in data.items():
            setattr(contest, field, value)
        await audit(
            db,
            action="contest.update",
            resource_type="contest",
            resource_id=contest.id,
            detail=audit_detail,
            actor_id=admin.id,
            actor_name=admin.name,
            request=request,
        )
        await db.commit()
        await db.refresh(contest)
    row = await _load_contest_with_count(db, contest_id)
    return _contest_out(row[0], row[1])


@router.delete("/{contest_id}", status_code=204)
async def delete_contest(
    contest_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(CONTEST_DELETE)),
) -> Response:
    contest = await db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    essay_count = await db.scalar(
        select(func.count()).select_from(Essay).where(Essay.contest_id == contest_id)
    )
    if essay_count:
        raise HTTPException(status_code=409, detail="该活动已有投稿，无法删除")
    await audit(
        db,
        action="contest.delete",
        resource_type="contest",
        resource_id=contest.id,
        detail={"title": contest.title},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.delete(contest)
    await db.commit()
    return Response(status_code=204)


@router.post("/{contest_id}/status", response_model=ContestOut)
async def update_contest_status(
    contest_id: int,
    body: ContestStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(CONTEST_MANAGE)),
) -> ContestOut:
    contest = await db.get(Contest, contest_id)
    if contest is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    previous = contest.status
    contest.status = body.status.value
    await audit(
        db,
        action="contest.status",
        resource_type="contest",
        resource_id=contest.id,
        detail={"from": previous, "to": contest.status},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(contest)
    row = await _load_contest_with_count(db, contest_id)
    return _contest_out(row[0], row[1])
