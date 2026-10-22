"""后台 · 触达工作台接口（工作二切片 B，契约 docs/api.md 第五部分 · 七）。

权限：outreach.read（编辑含，查询）/ outreach.manage（仅管理员：转人工、领取/退回、
提交结果、确认/驳回、取消、交接）。无任何发送能力；通道未决策时意图保持待通道配置。
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, pagination_params
from app.core.permissions import require_permission
from app.models import OutreachAttempt, OutreachIntent, StewardStaff, User
from app.schemas.common import Page, mask_phone
from app.schemas.outreach import (
    AssignIn,
    CancelIn,
    HandoverIn,
    HandoverOut,
    IntentDetailOut,
    IntentOut,
    NoteIn,
    RejectIn,
    ReleaseIn,
    RouteManualIn,
    SubmitIn,
    SummaryOut,
)
from app.services import outreach_service as svc
from app.services import steward_service

router = APIRouter(prefix="/admin/outreach", tags=["admin-outreach"])


def _intent_out(db: AsyncSession, intent: OutreachIntent, *, staff_name: str | None = None) -> IntentOut:
    return IntentOut(
        id=intent.id,
        instance_id=intent.instance_id,
        user_id=intent.user_id,
        task_id=intent.task_id,
        service_date=intent.service_date,
        purpose=intent.purpose,
        content_version=intent.content_version,
        status=intent.status,
        contactable=bool(intent.contactable),
        contact_note=intent.contact_note,
        assignee_staff_id=intent.assignee_staff_id,
        assignee_name=staff_name,
        attempts=intent.attempts or 0,
        stop_reason=intent.stop_reason,
        channel_receipt_ref=None,  # 通道接入前恒 null（契约固定形状，不读库内字段）
        result=intent.result,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
    )


async def _intent_detail(db: AsyncSession, intent: OutreachIntent) -> IntentDetailOut:
    staff = (
        await db.get(StewardStaff, intent.assignee_staff_id)
        if intent.assignee_staff_id
        else None
    )
    user = await db.get(User, intent.user_id)
    data = _intent_out(db, intent, staff_name=staff.name if staff else None).model_dump()
    data["user_nickname"] = user.nickname if user else None
    data["user_phone_masked"] = mask_phone(user.phone) if user else None
    logs = (
        (
            await db.execute(
                select(OutreachAttempt)
                .where(OutreachAttempt.intent_id == intent.id)
                .order_by(OutreachAttempt.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return IntentDetailOut(**data, attempts_log=[_attempt_out(log) for log in logs])


def _attempt_out(log: OutreachAttempt):
    from app.schemas.outreach import AttemptOut

    return AttemptOut(
        id=log.id,
        intent_id=log.intent_id,
        action=log.action,
        actor_type=log.actor_type,
        actor_admin_id=log.actor_admin_id,
        actor_name=log.actor_name,
        contact_method=log.contact_method,
        outcome=log.outcome,
        detail=log.detail,
        created_at=log.created_at,
    )


@router.get("/intents", response_model=Page[IntentOut])
async def list_intents(
    status: str | None = Query(default=None, max_length=16),
    instance_id: int | None = Query(default=None),
    assignee_staff_id: int | None = Query(default=None),
    service_date: date | None = Query(default=None),
    q: str | None = Query(default=None, max_length=64),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("outreach.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[IntentOut]:
    page, page_size = pagination
    filters = []
    if status:
        filters.append(OutreachIntent.status == status)
    if instance_id:
        filters.append(OutreachIntent.instance_id == instance_id)
    if assignee_staff_id:
        filters.append(OutreachIntent.assignee_staff_id == assignee_staff_id)
    if service_date:
        filters.append(OutreachIntent.service_date == service_date)
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(User.nickname.like(like), User.phone.like(like)))
    count_stmt = select(func.count()).select_from(OutreachIntent).join(
        User, OutreachIntent.user_id == User.id
    )
    list_stmt = (
        select(OutreachIntent)
        .join(User, OutreachIntent.user_id == User.id)
        .order_by(OutreachIntent.id.desc())
    )
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar()
    rows = (
        (
            await db.execute(
                list_stmt.offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    staff_ids = {r.assignee_staff_id for r in rows if r.assignee_staff_id}
    staff_map: dict[int, str] = {}
    for sid in staff_ids:
        staff = await db.get(StewardStaff, sid)
        if staff is not None:
            staff_map[sid] = staff.name
    items = []
    for intent in rows:
        data = _intent_out(
            db, intent, staff_name=staff_map.get(intent.assignee_staff_id or -1)
        ).model_dump()
        user = await db.get(User, intent.user_id)
        data["user_nickname"] = user.nickname if user else None
        data["user_phone_masked"] = mask_phone(user.phone) if user else None
        items.append(IntentOut(**data))
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.get("/intents/{intent_id}", response_model=IntentDetailOut)
async def intent_detail(
    intent_id: int,
    admin=Depends(require_permission("outreach.read")),
    db: AsyncSession = Depends(get_db),
) -> IntentDetailOut:
    intent = await db.get(OutreachIntent, intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="触达意图不存在")
    return await _intent_detail(db, intent)


@router.post("/intents/{intent_id}/route-manual", response_model=IntentDetailOut)
async def route_manual(
    intent_id: int,
    payload: RouteManualIn,
    admin=Depends(require_permission("outreach.manage")),
    db: AsyncSession = Depends(get_db),
) -> IntentDetailOut:
    intent = await svc.route_manual(db, admin, intent_id, payload.assignee_staff_id, payload.note)
    return await _intent_detail(db, intent)


@router.post("/intents/{intent_id}/assign", response_model=IntentDetailOut)
async def assign_intent(
    intent_id: int,
    payload: AssignIn,
    admin=Depends(require_permission("outreach.manage")),
    db: AsyncSession = Depends(get_db),
) -> IntentDetailOut:
    intent = await svc.assign(db, admin, intent_id, payload.assignee_staff_id, payload.note)
    return await _intent_detail(db, intent)


@router.post("/intents/{intent_id}/release", response_model=IntentDetailOut)
async def release_intent(
    intent_id: int,
    payload: ReleaseIn,
    admin=Depends(require_permission("outreach.manage")),
    db: AsyncSession = Depends(get_db),
) -> IntentDetailOut:
    intent = await svc.release(db, admin, intent_id, payload.reason)
    return await _intent_detail(db, intent)


@router.post("/intents/{intent_id}/submit", response_model=IntentDetailOut)
async def submit_intent(
    intent_id: int,
    payload: SubmitIn,
    admin=Depends(require_permission("outreach.manage")),
    db: AsyncSession = Depends(get_db),
) -> IntentDetailOut:
    intent = await svc.submit_result(
        db, admin, intent_id, payload.contact_method, payload.outcome, payload.note
    )
    return await _intent_detail(db, intent)


@router.post("/intents/{intent_id}/confirm", response_model=IntentDetailOut)
async def confirm_intent(
    intent_id: int,
    payload: NoteIn,
    admin=Depends(require_permission("outreach.manage")),
    db: AsyncSession = Depends(get_db),
) -> IntentDetailOut:
    intent = await svc.confirm(db, admin, intent_id, payload.note)
    return await _intent_detail(db, intent)


@router.post("/intents/{intent_id}/reject", response_model=IntentDetailOut)
async def reject_intent(
    intent_id: int,
    payload: RejectIn,
    admin=Depends(require_permission("outreach.manage")),
    db: AsyncSession = Depends(get_db),
) -> IntentDetailOut:
    intent = await svc.reject(db, admin, intent_id, payload.reason)
    return await _intent_detail(db, intent)


@router.post("/intents/{intent_id}/cancel", response_model=IntentDetailOut)
async def cancel_intent(
    intent_id: int,
    payload: CancelIn,
    admin=Depends(require_permission("outreach.manage")),
    db: AsyncSession = Depends(get_db),
) -> IntentDetailOut:
    intent = await svc.cancel_intent(db, admin, intent_id, payload.reason)
    return await _intent_detail(db, intent)


@router.post("/handover", response_model=HandoverOut)
async def handover(
    payload: HandoverIn,
    admin=Depends(require_permission("outreach.manage")),
    db: AsyncSession = Depends(get_db),
) -> HandoverOut:
    moved = await svc.handover(
        db, admin, payload.from_staff_id, payload.to_staff_id, payload.reason
    )
    return HandoverOut(moved=moved, from_staff_id=payload.from_staff_id, to_staff_id=payload.to_staff_id)


@router.get("/summary", response_model=SummaryOut)
async def summary(
    date_param: date | None = Query(default=None, alias="date"),
    admin=Depends(require_permission("outreach.read")),
    db: AsyncSession = Depends(get_db),
) -> SummaryOut:
    day = date_param or steward_service.sh_today()
    rows = (
        await db.execute(
            select(OutreachIntent.status, func.count())
            .where(OutreachIntent.service_date == day)
            .group_by(OutreachIntent.status)
        )
    ).all()
    by_status = {status: int(count) for status, count in rows}
    return SummaryOut(date=day, total=sum(by_status.values()), by_status=by_status)
