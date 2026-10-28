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
    ConsentRecordOut,
    InstanceDetailOut,
    InstanceOut,
    PrepCheckOut,
    StewardTaskOut,
    StaffRef,
    TimelineOut,
    ScheduleIntentIn,
    ScheduleIntentOut,
    TaskCompleteIn,
    TermsOut,
)
from app.schemas.commerce import BenefitItem, ConsentOut
from app.schemas.outreach import SubscriptionSetIn
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
        actual_occurred_at=task.actual_occurred_at,
        backfill_reason=task.backfill_reason,
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
    # 激活放行状态（S2）：仅 preparing 期展示；已激活实例不再重复判定
    if instance.status == "preparing":
        release_reason = await svc.release_missing_reason(db, instance)
        data["pilot_release_ready"] = release_reason is None
        data["pilot_release_reason"] = release_reason
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
    task = await svc.complete_user_task(
        db, user, instance, task_id, payload.result, payload.backfill_reason
    )
    return _task_out(task)


@router.post(
    "/instances/{instance_id}/tasks/{task_id}/schedule-intent",
    response_model=ScheduleIntentOut,
)
async def submit_schedule_intent(
    instance_id: int,
    task_id: int,
    payload: ScheduleIntentIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScheduleIntentOut:
    """随访预约意向：本人 pending 随访任务可申报方便时段；意向非确认，服务方处理留痕。"""
    instance = await svc.instance_for_user(db, user, instance_id)
    intent = await svc.submit_schedule_intent(
        db, user, instance, task_id, payload.preferred_date, payload.preferred_period, payload.note
    )
    return ScheduleIntentOut(
        id=intent.id,
        task_id=intent.task_id,
        instance_id=intent.instance_id,
        preferred_date=intent.preferred_date,
        preferred_period=intent.preferred_period,
        note=intent.note,
        status=intent.status,
        handle_note=intent.handle_note,
        handled_at=intent.handled_at,
        created_at=intent.created_at,
    )


@router.get("/instances/{instance_id}/professional")
async def instance_professional(
    instance_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """专业进展聚合（切片 D）：工单进度 / 版本化报告 / 随访记录 + 固定急救文案。"""
    from app.schemas.steward import ProfessionalViewOut
    from app.services import professional_service

    instance = await svc.instance_for_user(db, user, instance_id)
    data = await professional_service.instance_professional_view(db, instance)
    return ProfessionalViewOut(**data).model_dump()


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


@router.get("/consent-terms", response_model=list[TermsOut])
async def consent_terms(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TermsOut]:
    """当前有效条款全文（S1：授权前展示、确认后提交所见版本）。"""
    rows = await svc.current_terms_all(db)
    return [
        TermsOut(
            id=t.id,
            purpose=t.purpose,
            version=t.version,
            title=t.title,
            text=t.text,
            effective_from=t.effective_from,
            status=t.status,
            note=t.note,
        )
        for t in rows
    ]


@router.get("/consent-records", response_model=list[ConsentRecordOut])
async def consent_records(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConsentRecordOut]:
    """本人各用途授权现状；outdated=true 须重新确认新版本条款。"""
    records = await svc.consent_records(db, user)
    return [ConsentRecordOut(**r) for r in records]


@router.post("/consents", response_model=ConsentOut)
async def grant_consent(
    payload: ConsentGrantIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConsentOut:
    consent = await svc.grant_steward_consent(db, user, payload.purpose, payload.version)
    return ConsentOut(
        id=consent.id,
        purpose=consent.purpose,
        version=consent.version,
        text=consent.text,
        granted_at=consent.granted_at,
        revoked_at=consent.revoked_at,
    )


# ───────────────── 电话通路状态（切片 E，真实外呼关闭） ─────────────────


@router.get("/phone/status")
async def phone_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """本人电话通路状态：三类授权现状、联系人（掩码）、近期工单状态；不含通话内容。"""
    from app.schemas.phone import PhoneStatusOut
    from app.services import phone_service

    data = await phone_service.user_phone_status(db, user)
    return PhoneStatusOut(**data).model_dump()


# ───────────────── 触达状态与退订（切片 B，渠道无关） ─────────────────


@router.get("/outreach/status")
async def outreach_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """本人触达状态：通道未决策时如实展示待配置与人工跟进，不承诺每日送达。"""
    from app.schemas.outreach import SubscriptionStatusOut
    from app.services import outreach_service

    data = await outreach_service.subscription_status(db, user.id)
    return SubscriptionStatusOut(**data).model_dump()


@router.post("/outreach/subscription")
async def set_outreach_subscription(
    payload: SubscriptionSetIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """退订 / 重新订阅每日提醒。退订立即停止本人全部未完成触达；不影响履约，不等于退款。"""
    from app.schemas.outreach import SubscriptionStatusOut
    from app.services import outreach_service

    data = await outreach_service.set_subscription(db, user, payload.subscribed)
    return SubscriptionStatusOut(
        **{k: v for k, v in data.items() if k != "stopped"}
    ).model_dump() | {"stopped": data.get("stopped", 0)}
