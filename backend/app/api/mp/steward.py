"""C 端 · 服务履约接口（契约 docs/api.md 第五部分 · 一）。

页面只呈现服务端事实：准备中显示缺项清单，不承诺发送与到达；
激活门禁与状态迁移全部由服务层判定。
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models import StewardStaff, StewardTask, User
from app.schemas.steward import (
    CalendarDayOut,
    ConsentGrantIn,
    InstanceDetailOut,
    InstanceOut,
    PrepCheckOut,
    StewardTaskOut,
    StaffRef,
    TimelineOut,
    TaskCompleteIn,
)
from app.schemas.commerce import BenefitItem, ConsentOut
from app.services import steward_service as svc
from app.schemas.common import mask_phone

router = APIRouter(prefix="/steward", tags=["mp-steward"])


def _staff_ref(staff: StewardStaff | None) -> StaffRef | None:
    if staff is None:
        return None
    return StaffRef(id=staff.id, name=staff.name, role=staff.role, phone_masked=mask_phone(staff.phone))


def _benefits(instance) -> list[BenefitItem]:
    items = []
    for raw in instance.benefits or []:
        if isinstance(raw, dict):
            items.append(BenefitItem(name=str(raw.get("name", ""))[:50], desc=raw.get("desc")))
    return items


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


async def instance_out(db: AsyncSession, instance, *, detail: bool = False) -> dict:
    """组装实例 DTO；detail 时附带门禁、今日任务与进行中的暂停。"""
    butler = await db.get(StewardStaff, instance.butler_id) if instance.butler_id else None
    backup = (
        await db.get(StewardStaff, instance.backup_butler_id) if instance.backup_butler_id else None
    )
    physician = await db.get(StewardStaff, instance.physician_id) if instance.physician_id else None
    current_day = await svc.current_service_day(db, instance)
    data = dict(
        id=instance.id,
        instance_no=instance.instance_no,
        entitlement_id=instance.entitlement_id,
        package_name=instance.package_name,
        version_no=instance.version_no,
        benefits=_benefits(instance),
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
    )
    if not detail:
        return data
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
    today_tasks = await svc.tasks_on(db, instance, today)
    data["today_tasks"] = [_task_out(t) for t in today_tasks]
    return data


@router.get("/instances", response_model=list[InstanceOut])
async def my_instances(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[InstanceOut]:
    instances = await svc.ensure_instances_for_user(db, user)
    return [InstanceOut(**(await instance_out(db, inst))) for inst in instances]


@router.get("/instances/{instance_id}", response_model=InstanceDetailOut)
async def my_instance_detail(
    instance_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InstanceDetailOut:
    instance = await svc.instance_for_user(db, user, instance_id)
    data = await instance_out(db, instance, detail=True)
    data["user_id"] = instance.user_id
    return InstanceDetailOut(**data)


@router.post("/instances/{instance_id}/activate", response_model=InstanceDetailOut)
async def activate(
    instance_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> InstanceDetailOut:
    instance = await svc.instance_for_user(db, user, instance_id)
    instance = await svc.activate_instance(db, user, instance)
    data = await instance_out(db, instance, detail=True)
    data["user_id"] = instance.user_id
    return InstanceDetailOut(**data)


@router.get("/instances/{instance_id}/calendar", response_model=list[CalendarDayOut])
async def calendar(
    instance_id: int,
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CalendarDayOut]:
    instance = await svc.instance_for_user(db, user, instance_id)
    year, month_no = (int(part) for part in month.split("-"))
    month_first = date(year, month_no, 1)
    days = await svc.calendar_month(db, instance, month_first)
    return [
        CalendarDayOut(
            date=day["date"], service_day=day["service_day"], tasks=[_task_out(t) for t in day["tasks"]]
        )
        for day in days
    ]


@router.get("/instances/{instance_id}/tasks", response_model=list[StewardTaskOut])
async def my_tasks(
    instance_id: int,
    service_day: int | None = Query(default=None, ge=1),
    date_str: str | None = Query(default=None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[StewardTaskOut]:
    instance = await svc.instance_for_user(db, user, instance_id)
    if service_day is not None:
        result = await db.execute(
            select(StewardTask)
            .where(
                StewardTask.instance_id == instance.id, StewardTask.service_day == service_day
            )
            .order_by(StewardTask.task_type.desc())
        )
        tasks = result.scalars().all()
    else:
        target = date.fromisoformat(date_str) if date_str else svc.sh_today()
        tasks = await svc.tasks_on(db, instance, target)
    return [_task_out(t) for t in tasks]


@router.post("/instances/{instance_id}/tasks/{task_id}/complete", response_model=StewardTaskOut)
async def complete_task(
    instance_id: int,
    task_id: int,
    payload: TaskCompleteIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StewardTaskOut:
    instance = await svc.instance_for_user(db, user, instance_id)
    task = await svc.complete_user_task(db, user, instance, task_id, payload.result)
    return _task_out(task)


@router.get("/instances/{instance_id}/timeline", response_model=list[TimelineOut])
async def my_timeline(
    instance_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TimelineOut]:
    from app.models import StewardTimelineEvent

    instance = await svc.instance_for_user(db, user, instance_id)
    result = await db.execute(
        select(StewardTimelineEvent)
        .where(StewardTimelineEvent.instance_id == instance.id)
        .order_by(StewardTimelineEvent.id.desc())
        .limit(100)
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


@router.post("/consents", response_model=ConsentOut)
async def grant_consent(
    payload: ConsentGrantIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConsentOut:
    consent = await svc.grant_steward_consent(db, user, payload.purpose)
    return ConsentOut(
        id=consent.id,
        purpose=consent.purpose,
        version=consent.version,
        text=consent.text,
        granted_at=consent.granted_at,
        revoked_at=consent.revoked_at,
    )
