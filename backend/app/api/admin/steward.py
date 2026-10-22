"""后台 · 服务履约工作台接口（契约 docs/api.md 第五部分 · 二）。

权限：steward.read（查询）/ steward.manage（门禁录入、指派、暂停 / 恢复、结案、
任务登记、人员管理）。查询显示用户手机号一律掩码；审计随业务同事务。
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, pagination_params
from app.core.permissions import require_permission
from app.models import (
    StewardPauseRecord,
    StewardServiceInstance,
    StewardStaff,
    StewardTask,
    StewardTimelineEvent,
    User,
)
from app.schemas.common import Page, mask_phone
from app.schemas.steward import (
    AssignIn,
    CalendarDayOut,
    CloseIn,
    InstanceDetailOut,
    PauseIn,
    PauseOut,
    PrepPassIn,
    PrepResetIn,
    PrepCheckOut,
    ResumeIn,
    StaffCreateIn,
    StaffOut,
    StaffRef,
    StaffUpdateIn,
    StewardTaskOut,
    TaskResultIn,
    TimelineOut,
)
from app.services import steward_service as svc

router = APIRouter(prefix="/admin/steward", tags=["admin-steward"])


def _staff_ref(staff: StewardStaff | None) -> StaffRef | None:
    if staff is None:
        return None
    return StaffRef(id=staff.id, name=staff.name, role=staff.role, phone_masked=mask_phone(staff.phone))


def _task_out(task: StewardTask) -> StewardTaskOut:
    return StewardTaskOut(
        id=task.id,
        service_day=task.service_day,
        service_date=task.service_date,
        task_type=task.task_type,
        title=task.title,
        assignee_type=task.assignee_type,
        status=task.status,
        due_at=task.due_at,
        result=task.result,
        completed_at=task.completed_at,
        shifted_days=task.shifted_days,
    )


async def _instance_base(db: AsyncSession, instance: StewardServiceInstance) -> dict:
    butler = await db.get(StewardStaff, instance.butler_id) if instance.butler_id else None
    backup = (
        await db.get(StewardStaff, instance.backup_butler_id) if instance.backup_butler_id else None
    )
    physician = await db.get(StewardStaff, instance.physician_id) if instance.physician_id else None
    user = await db.get(User, instance.user_id)
    benefits = [
        {"name": str(raw.get("name", ""))[:50], "desc": raw.get("desc")}
        for raw in (instance.benefits or [])
        if isinstance(raw, dict)
    ]
    current_day = await svc.current_service_day(db, instance)
    return dict(
        id=instance.id,
        instance_no=instance.instance_no,
        entitlement_id=instance.entitlement_id,
        package_name=instance.package_name,
        version_no=instance.version_no,
        benefits=benefits,
        validity_days=instance.validity_days,
        service_days=instance.service_days,
        status=instance.status,
        activated_at=instance.activated_at,
        ends_at=instance.ends_at,
        original_ends_at=instance.original_ends_at,
        compensated_seconds=instance.compensated_seconds,
        current_day=current_day,
        butler=_staff_ref(butler),
        backup_butler=_staff_ref(backup),
        physician=_staff_ref(physician),
        plan_version=instance.plan_version,
        renewed_from_instance_id=instance.renewed_from_instance_id,
        termination_reason=instance.termination_reason,
        terminated_at=instance.terminated_at,
        closed_at=instance.closed_at,
        created_at=instance.created_at,
        user_id=instance.user_id,
        user_nickname=(user.nickname if user else None),
        user_phone_masked=(mask_phone(user.phone) if user else None),
    )


@router.get("/instances", response_model=Page[InstanceDetailOut])
async def list_instances(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=64),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[InstanceDetailOut]:
    page, page_size = pagination
    filters = []
    if status:
        filters.append(StewardServiceInstance.status == status)
    if q:
        like = f"%{q.strip()}%"
        filters.append(
            or_(
                StewardServiceInstance.instance_no.like(like),
                StewardServiceInstance.package_name.like(like),
                User.nickname.like(like),
                User.phone.like(like),
            )
        )
    count_stmt = select(func.count()).select_from(StewardServiceInstance).join(
        User, StewardServiceInstance.user_id == User.id
    )
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar()
    list_stmt = (
        select(StewardServiceInstance)
        .join(User, StewardServiceInstance.user_id == User.id)
        .order_by(StewardServiceInstance.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if filters:
        list_stmt = list_stmt.where(*filters)
    rows = (await db.execute(list_stmt)).scalars().all()
    items = []
    for instance in rows:
        data = await _instance_base(db, instance)
        data["prep_checks"] = []
        data["today_tasks"] = []
        items.append(InstanceDetailOut(**data))
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.get("/instances/{instance_id}", response_model=InstanceDetailOut)
async def instance_detail(
    instance_id: int,
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> InstanceDetailOut:
    instance = await svc.instance_for_admin(db, instance_id)
    data = await _instance_base(db, instance)
    checks = await svc.evaluate_prep_checks(db, instance)
    data["prep_checks"] = [
        PrepCheckOut(
            check_type=row.check_type,
            title=svc.PREP_CHECK_META[row.check_type][0],
            source=row.source,
            status=row.status,
            missing_reason=(
                row.missing_reason
                or ("待运营录入完成凭据" if row.source == "manual" else row.missing_reason)
            ),
            evidence=row.evidence,
            completed_at=row.completed_at,
        )
        for row in checks
    ]
    today = svc.sh_today()
    data["today_tasks"] = [_task_out(t) for t in await svc.tasks_on(db, instance, today)]
    task_rows = (
        (
            await db.execute(
                select(StewardTask.task_type, StewardTask.status, func.count())
                .where(StewardTask.instance_id == instance.id)
                .group_by(StewardTask.task_type, StewardTask.status)
            )
        ).all()
    )
    summary: dict = {}
    for task_type, task_status, count in task_rows:
        summary.setdefault(task_type, {})[task_status] = int(count)
    data["task_summary"] = summary
    pauses = (
        (
            await db.execute(
                select(StewardPauseRecord)
                .where(StewardPauseRecord.instance_id == instance.id)
                .order_by(StewardPauseRecord.id.desc())
            )
        )
        .scalars()
        .all()
    )
    data["pauses"] = [_pause_out(p) for p in pauses]
    return InstanceDetailOut(**data)


def _pause_out(p: StewardPauseRecord) -> PauseOut:
    return PauseOut(
        id=p.id,
        cause=p.cause,
        reason=p.reason,
        started_at=p.started_at,
        ended_at=p.ended_at,
        duration_seconds=p.duration_seconds,
        compensated_seconds=p.compensated_seconds,
        tasks_shifted_days=p.tasks_shifted_days,
        note=p.note,
    )


@router.get("/instances/{instance_id}/calendar", response_model=list[CalendarDayOut])
async def instance_calendar(
    instance_id: int,
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[CalendarDayOut]:
    instance = await svc.instance_for_admin(db, instance_id)
    year, month_no = (int(part) for part in month.split("-"))
    days = await svc.calendar_month(db, instance, date(year, month_no, 1))
    return [
        CalendarDayOut(
            date=day["date"], service_day=day["service_day"], tasks=[_task_out(t) for t in day["tasks"]]
        )
        for day in days
    ]


@router.get("/instances/{instance_id}/timeline", response_model=list[TimelineOut])
async def instance_timeline(
    instance_id: int,
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[TimelineOut]:
    await svc.instance_for_admin(db, instance_id)
    result = await db.execute(
        select(StewardTimelineEvent)
        .where(StewardTimelineEvent.instance_id == instance_id)
        .order_by(StewardTimelineEvent.id.desc())
        .limit(200)
    )
    events = result.scalars().all()
    return [
        TimelineOut(
            id=event.id,
            event_type=event.event_type,
            detail=event.detail,
            actor_type=event.actor_type,
            actor_name=event.actor_name,
            created_at=event.created_at,
        )
        for event in events
    ]


@router.post(
    "/instances/{instance_id}/prep-checks/{check_type}/pass", response_model=PrepCheckOut
)
async def pass_check(
    instance_id: int,
    check_type: str,
    payload: PrepPassIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> PrepCheckOut:
    instance = await svc.instance_for_admin(db, instance_id)
    row = await svc.pass_prep_check(db, admin, instance, check_type, payload.evidence)
    return PrepCheckOut(
        check_type=row.check_type,
        title=svc.PREP_CHECK_META[row.check_type][0],
        source=row.source,
        status=row.status,
        missing_reason=row.missing_reason,
        evidence=row.evidence,
        completed_at=row.completed_at,
    )


@router.post(
    "/instances/{instance_id}/prep-checks/{check_type}/reset", response_model=PrepCheckOut
)
async def reset_check(
    instance_id: int,
    check_type: str,
    payload: PrepResetIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> PrepCheckOut:
    instance = await svc.instance_for_admin(db, instance_id)
    row = await svc.reset_prep_check(db, admin, instance, check_type, payload.reason)
    return PrepCheckOut(
        check_type=row.check_type,
        title=svc.PREP_CHECK_META[row.check_type][0],
        source=row.source,
        status=row.status,
        missing_reason=row.missing_reason,
        evidence=row.evidence,
        completed_at=row.completed_at,
    )


@router.post("/instances/{instance_id}/assign", response_model=InstanceDetailOut)
async def assign(
    instance_id: int,
    payload: AssignIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> InstanceDetailOut:
    instance = await svc.instance_for_admin(db, instance_id)
    instance = await svc.assign_staff(
        db, admin, instance, payload.model_dump(exclude_unset=True)
    )
    data = await _instance_base(db, instance)
    data["prep_checks"] = []
    data["today_tasks"] = []
    return InstanceDetailOut(**data)


@router.post("/instances/{instance_id}/pause", response_model=InstanceDetailOut)
async def pause(
    instance_id: int,
    payload: PauseIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> InstanceDetailOut:
    instance = await svc.instance_for_admin(db, instance_id)
    instance = await svc.pause_instance(db, admin, instance, payload.cause, payload.reason)
    data = await _instance_base(db, instance)
    data["prep_checks"] = []
    data["today_tasks"] = []
    return InstanceDetailOut(**data)


@router.post("/instances/{instance_id}/resume", response_model=InstanceDetailOut)
async def resume(
    instance_id: int,
    payload: ResumeIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> InstanceDetailOut:
    instance = await svc.instance_for_admin(db, instance_id)
    instance = await svc.resume_instance(db, admin, instance, payload.note)
    data = await _instance_base(db, instance)
    data["prep_checks"] = []
    data["today_tasks"] = []
    return InstanceDetailOut(**data)


@router.post("/instances/{instance_id}/close", response_model=InstanceDetailOut)
async def close(
    instance_id: int,
    payload: CloseIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> InstanceDetailOut:
    instance = await svc.instance_for_admin(db, instance_id)
    instance = await svc.close_instance(db, admin, instance, payload.closure_report)
    data = await _instance_base(db, instance)
    data["prep_checks"] = []
    data["today_tasks"] = []
    return InstanceDetailOut(**data)


@router.post("/tasks/{task_id}/result", response_model=StewardTaskOut)
async def task_result(
    task_id: int,
    payload: TaskResultIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> StewardTaskOut:
    task = await svc.record_task_result(db, admin, task_id, payload.result)
    return _task_out(task)


# ───────────────────────── 履约人员 ─────────────────────────


def _staff_out(staff: StewardStaff) -> StaffOut:
    return StaffOut(
        id=staff.id,
        name=staff.name,
        role=staff.role,
        phone_masked=mask_phone(staff.phone),
        org_name=staff.org_name,
        title=staff.title,
        license_no=staff.license_no,
        status=staff.status,
        note=staff.note,
        created_at=staff.created_at,
    )


@router.get("/staff", response_model=Page[StaffOut])
async def list_staff(
    role: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[StaffOut]:
    page, page_size = pagination
    filters = []
    if role:
        filters.append(StewardStaff.role == role)
    count_stmt = select(func.count()).select_from(StewardStaff)
    list_stmt = select(StewardStaff).order_by(StewardStaff.id.desc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar()
    rows = (
        (await db.execute(list_stmt.offset((page - 1) * page_size).limit(page_size)))
        .scalars()
        .all()
    )
    return Page(items=[_staff_out(s) for s in rows], total=int(total or 0), page=page, page_size=page_size)


@router.post("/staff", response_model=StaffOut, status_code=201)
async def create_staff(
    payload: StaffCreateIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> StaffOut:
    staff = await svc.create_staff(db, admin, payload.model_dump())
    return _staff_out(staff)


@router.patch("/staff/{staff_id}", response_model=StaffOut)
async def update_staff(
    staff_id: int,
    payload: StaffUpdateIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> StaffOut:
    staff = await db.get(StewardStaff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    staff = await svc.update_staff(db, admin, staff, payload.model_dump(exclude_unset=True))
    return _staff_out(staff)
