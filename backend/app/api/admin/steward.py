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
    StewardCarePlan,
    StewardPauseRecord,
    StewardPilotRelease,
    StewardServiceInstance,
    StewardStaff,
    StewardTask,
    StewardTimelineEvent,
    User,
)
from app.schemas.common import Page, mask_phone
from app.schemas.steward import (
    AssignIn,
    CareRulesOut,
    CareRulesPublishIn,
    FollowupRecordIn,
    FollowupRecordOut,
    OrderCompleteIn,
    OrderCreateIn,
    OrderDetailOut,
    OrderOut,
    OrderTransferIn,
    ReasonIn,
    ReportCorrectIn,
    ReportOut,
    ScheduleIntentHandleIn,
    ScheduleIntentOut,
    StaffLinkCreateIn,
    StaffLinkOut,
    CalendarDayOut,
    CarePlanCreateIn,
    CarePlanOut,
    CarePlanRevokeIn,
    CarePlanVerifyIn,
    CloseIn,
    InstanceDetailOut,
    PauseIn,
    PauseOut,
    PrepPassIn,
    PrepResetIn,
    PrepCheckOut,
    ReleaseCreateIn,
    ReleaseOut,
    ReleaseRevokeIn,
    ResumeIn,
    ScheduleCreateIn,
    ScheduleOut,
    StaffCreateIn,
    StaffCredentialVerifyIn,
    StaffOut,
    StaffRef,
    StaffUpdateIn,
    StewardTaskOut,
    TaskResultIn,
    TermsOut,
    TermsPublishIn,
    TimelineOut,
)
from app.services import professional_service as prof
from app.services import scope_service as scope_svc
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
        actual_occurred_at=task.actual_occurred_at,
        backfill_reason=task.backfill_reason,
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
    scope = await scope_svc.load_scope(db, admin)
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
    filters.append(scope_svc.instance_predicate(scope))
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
    instance = await scope_svc.require_instance_access(db, admin, instance_id)
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
    instance = await scope_svc.require_instance_access(db, admin, instance_id, audit_cross_scope=False)
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
    await scope_svc.require_instance_access(db, admin, instance_id, audit_cross_scope=False)
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
    instance = await scope_svc.require_instance_access(db, admin, instance_id, audit_cross_scope=False)
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
    instance = await scope_svc.require_instance_access(db, admin, instance_id, audit_cross_scope=False)
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
    instance = await scope_svc.require_instance_access(db, admin, instance_id, audit_cross_scope=False)
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
    instance = await scope_svc.require_instance_access(db, admin, instance_id, audit_cross_scope=False)
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
    instance = await scope_svc.require_instance_access(db, admin, instance_id, audit_cross_scope=False)
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
    instance = await scope_svc.require_instance_access(db, admin, instance_id, audit_cross_scope=False)
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
    task = await db.get(StewardTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    await scope_svc.require_instance_access(db, admin, task.instance_id, audit_cross_scope=False)
    task = await svc.record_task_result(
        db, admin, task_id, payload.result, payload.actual_occurred_at, payload.backfill_reason
    )
    return _task_out(task)


# ───────────────────────── 授权条款（S1） ─────────────────────────


def _terms_out(t) -> TermsOut:
    return TermsOut(
        id=t.id,
        purpose=t.purpose,
        version=t.version,
        title=t.title,
        text=t.text,
        effective_from=t.effective_from,
        status=t.status,
        note=t.note,
    )


@router.get("/consent-terms", response_model=list[TermsOut])
async def list_consent_terms(
    purpose: str | None = Query(default=None, max_length=32),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[TermsOut]:
    return [_terms_out(t) for t in await svc.list_terms(db, purpose)]


@router.post("/consent-terms", response_model=TermsOut, status_code=201)
async def publish_consent_terms(
    payload: TermsPublishIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> TermsOut:
    terms = await svc.publish_terms(
        db, admin, payload.purpose, payload.version, payload.title, payload.text, payload.note
    )
    return _terms_out(terms)


# ───────────────────────── 试点放行（S2） ─────────────────────────


def _release_out(r) -> ReleaseOut:
    return ReleaseOut(
        id=r.id,
        package_id=r.package_id,
        package_version_id=r.package_version_id,
        env=r.env,
        owner_name=r.owner_name,
        evidence=r.evidence or [],
        effective_from=r.effective_from,
        effective_until=r.effective_until,
        revoked_at=r.revoked_at,
        revoke_reason=r.revoke_reason,
        created_at=r.created_at,
    )


@router.get("/pilot-releases", response_model=list[ReleaseOut])
async def list_pilot_releases(
    package_id: int | None = Query(default=None),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ReleaseOut]:
    return [_release_out(r) for r in await svc.list_releases(db, package_id)]


@router.post("/pilot-releases", response_model=ReleaseOut, status_code=201)
async def create_pilot_release(
    payload: ReleaseCreateIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> ReleaseOut:
    release = await svc.create_release(db, admin, payload.model_dump())
    return _release_out(release)


@router.post("/pilot-releases/{release_id}/revoke", response_model=ReleaseOut)
async def revoke_pilot_release(
    release_id: int,
    payload: ReleaseRevokeIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> ReleaseOut:
    release = await db.get(StewardPilotRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="放行记录不存在")
    release = await svc.revoke_release(db, admin, release, payload.reason)
    return _release_out(release)


# ───────────────────────── 医师签发计划（A1） ─────────────────────────


def _care_plan_out(p) -> CarePlanOut:
    return CarePlanOut(
        id=p.id,
        user_id=p.user_id,
        instance_id=p.instance_id,
        physician_id=p.physician_id,
        org_name=p.org_name,
        version=p.version,
        rules_version=p.rules_version,
        content=p.content or {},
        issued_at=p.issued_at,
        valid_until=p.valid_until,
        revoked_at=p.revoked_at,
        revoke_reason=p.revoke_reason,
        issue_source=p.issue_source,
        trusted=p.trusted,
        evidence_source=p.evidence_source,
        verified_by_admin_id=p.verified_by_admin_id,
        verified_at=p.verified_at,
        created_by_admin_id=p.created_by_admin_id,
        created_at=p.created_at,
    )


@router.get("/care-plans", response_model=list[CarePlanOut])
async def list_care_plans(
    user_id: int | None = Query(default=None),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[CarePlanOut]:
    scope = await scope_svc.load_scope(db, admin)
    plans = await svc.list_care_plans(
        db, user_id, extra_where=scope_svc.user_predicate(scope, StewardCarePlan.user_id)
    )
    return [_care_plan_out(p) for p in plans]


@router.post("/care-plans", response_model=CarePlanOut, status_code=201)
async def create_care_plan(
    payload: CarePlanCreateIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> CarePlanOut:
    await scope_svc.require_user_access(db, admin, payload.user_id, audit_cross_scope=False)
    plan = await svc.create_care_plan(db, admin, payload.model_dump())
    return _care_plan_out(plan)


@router.post("/care-plans/{plan_id}/verify", response_model=CarePlanOut)
async def verify_care_plan(
    plan_id: int,
    payload: CarePlanVerifyIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> CarePlanOut:
    """为运营录入的计划补外部签发材料并核验（C3）；当前账号为核验责任人。"""
    plan = await db.get(StewardCarePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="签发计划不存在")
    await scope_svc.require_user_access(db, admin, plan.user_id, audit_cross_scope=False)
    plan = await svc.verify_care_plan(db, admin, plan, payload.evidence_ref, payload.evidence_source, payload.note)
    return _care_plan_out(plan)


@router.post("/care-plans/{plan_id}/revoke", response_model=CarePlanOut)
async def revoke_care_plan(
    plan_id: int,
    payload: CarePlanRevokeIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> CarePlanOut:
    plan = await db.get(StewardCarePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="签发计划不存在")
    await scope_svc.require_user_access(db, admin, plan.user_id, audit_cross_scope=False)
    plan = await svc.revoke_care_plan(db, admin, plan, payload.reason)
    return _care_plan_out(plan)


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
        service_scope=staff.service_scope,
        credential_valid=staff.credential_valid(svc.now_utc()),
        credential_source=staff.credential_source,
        credential_verified_by_admin_id=staff.credential_verified_by_admin_id,
        credential_verified_at=staff.credential_verified_at,
        credential_valid_until=staff.credential_valid_until,
        credential_revoked_at=staff.credential_revoked_at,
        credential_revoke_reason=staff.credential_revoke_reason,
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


async def _staff_or_404(db: AsyncSession, staff_id: int) -> StewardStaff:
    staff = await db.get(StewardStaff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    return staff


def _schedule_out(row) -> ScheduleOut:
    return ScheduleOut(
        id=row.id, staff_id=row.staff_id, starts_on=row.starts_on, ends_on=row.ends_on, status=row.status,
        note=row.note, cancelled_at=row.cancelled_at, cancel_reason=row.cancel_reason, created_at=row.created_at,
    )


@router.post("/staff/{staff_id}/credential/verify", response_model=StaffOut)
async def verify_staff_credential(
    staff_id: int,
    payload: StaffCredentialVerifyIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> StaffOut:
    staff = await _staff_or_404(db, staff_id)
    staff = await svc.verify_staff_credential(
        db, admin, staff, credential_ref=payload.credential_ref, credential_source=payload.credential_source,
        valid_until=payload.valid_until, service_scope=payload.service_scope,
    )
    return _staff_out(staff)


@router.post("/staff/{staff_id}/credential/revoke", response_model=StaffOut)
async def revoke_staff_credential(
    staff_id: int,
    payload: ReasonIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> StaffOut:
    staff = await _staff_or_404(db, staff_id)
    return _staff_out(await svc.revoke_staff_credential(db, admin, staff, payload.reason))


@router.get("/staff/{staff_id}/schedules", response_model=list[ScheduleOut])
async def list_schedules(
    staff_id: int,
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ScheduleOut]:
    from app.models import StewardStaffSchedule

    await _staff_or_404(db, staff_id)
    rows = (
        await db.execute(
            select(StewardStaffSchedule)
            .where(StewardStaffSchedule.staff_id == staff_id)
            .order_by(StewardStaffSchedule.starts_on.desc(), StewardStaffSchedule.id.desc())
        )
    ).scalars().all()
    return [_schedule_out(r) for r in rows]


@router.post("/staff/{staff_id}/schedules", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    staff_id: int,
    payload: ScheduleCreateIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> ScheduleOut:
    staff = await _staff_or_404(db, staff_id)
    row = await svc.create_schedule(
        db, admin, staff, starts_on=payload.starts_on, ends_on=payload.ends_on, note=payload.note
    )
    return _schedule_out(row)


@router.post("/schedules/{schedule_id}/cancel", response_model=ScheduleOut)
async def cancel_schedule(
    schedule_id: int,
    payload: ReasonIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> ScheduleOut:
    from app.models import StewardStaffSchedule

    row = await db.get(StewardStaffSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="排班不存在")
    return _schedule_out(await svc.cancel_schedule(db, admin, row, payload.reason))


# ───────────────────── 专业任务与异常工单（切片 D） ─────────────────────


def _rules_out(r) -> CareRulesOut:
    return CareRulesOut(
        id=r.id, org_name=r.org_name, version=r.version, content=r.content or {},
        status=r.status, retired_at=r.retired_at, note=r.note, created_at=r.created_at,
    )


@router.get("/care-rules", response_model=list[CareRulesOut])
async def list_care_rules(
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[CareRulesOut]:
    return [_rules_out(r) for r in await prof.list_care_rules(db)]


@router.post("/care-rules", response_model=CareRulesOut, status_code=201)
async def publish_care_rules(
    payload: CareRulesPublishIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> CareRulesOut:
    rules = await prof.publish_care_rules(
        db, admin, payload.org_name, payload.version, payload.content, payload.note
    )
    return _rules_out(rules)


@router.get("/staff-links", response_model=list[StaffLinkOut])
async def list_staff_links(
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[StaffLinkOut]:
    from app.models import AdminUser, StewardStaffAdminLink

    rows = (
        (await db.execute(select(StewardStaffAdminLink).order_by(StewardStaffAdminLink.id.desc())))
        .scalars()
        .all()
    )
    items = []
    for link in rows:
        staff = await db.get(StewardStaff, link.staff_id)
        target = await db.get(AdminUser, link.admin_id)
        items.append(
            StaffLinkOut(
                id=link.id, staff_id=link.staff_id, staff_name=staff.name if staff else None,
                admin_id=link.admin_id, admin_username=target.username if target else None,
                created_at=link.created_at,
            )
        )
    return items


@router.post("/staff-links", status_code=201)
async def create_staff_link(
    payload: StaffLinkCreateIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await prof.create_staff_link(db, admin, payload.staff_id, payload.admin_id)
    return {"ok": True}


@router.delete("/staff-links/{link_id}")
async def delete_staff_link(
    link_id: int,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await prof.delete_staff_link(db, admin, link_id)
    return {"ok": True}


async def _order_out(db: AsyncSession, o) -> OrderOut:
    physician = await db.get(StewardStaff, o.physician_id)
    return OrderOut(
        id=o.id, instance_id=o.instance_id, user_id=o.user_id, order_type=o.order_type,
        title=o.title, detail=o.detail, priority=o.priority, status=o.status,
        physician_id=o.physician_id, physician_name=physician.name if physician else None,
        task_id=o.task_id, care_plan_version=o.care_plan_version, rules_version=o.rules_version,
        due_at=o.due_at, escalated_at=o.escalated_at, transferred_at=o.transferred_at,
        transfer_reason=o.transfer_reason, result=o.result, completed_at=o.completed_at,
        created_at=o.created_at,
    )


@router.get("/professional/orders", response_model=Page[OrderOut])
async def list_professional_orders(
    instance_id: int | None = Query(default=None),
    status: str | None = Query(default=None, max_length=12),
    priority: str | None = Query(default=None, max_length=8),
    order_type: str | None = Query(default=None, max_length=24),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[OrderOut]:
    from app.models import StewardProfessionalOrder

    page, page_size = pagination
    scope = await scope_svc.load_scope(db, admin)
    filters = [scope_svc.instance_predicate(scope, StewardProfessionalOrder.instance_id)]
    if instance_id:
        filters.append(StewardProfessionalOrder.instance_id == instance_id)
    if status:
        filters.append(StewardProfessionalOrder.status == status)
    if priority:
        filters.append(StewardProfessionalOrder.priority == priority)
    if order_type:
        filters.append(StewardProfessionalOrder.order_type == order_type)
    count_stmt = select(func.count()).select_from(StewardProfessionalOrder).where(*filters)
    list_stmt = select(StewardProfessionalOrder).where(*filters).order_by(StewardProfessionalOrder.id.desc())
    total = (await db.execute(count_stmt)).scalar()
    rows = (
        (await db.execute(list_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    )
    items = [await _order_out(db, o) for o in rows]
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.post("/professional/orders", response_model=OrderOut, status_code=201)
async def create_professional_order(
    payload: OrderCreateIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await prof.create_order(db, admin, payload.model_dump())
    return await _order_out(db, order)


@router.get("/professional/orders/{order_id}", response_model=OrderDetailOut)
async def professional_order_detail(
    order_id: int,
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> OrderDetailOut:
    from app.models import StewardProfessionalEvent, StewardProfessionalOrder
    from app.schemas.steward import OrderEventOut

    order = await db.get(StewardProfessionalOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    await scope_svc.require_instance_access(db, admin, order.instance_id)
    base = await _order_out(db, order)
    events = (
        (
            await db.execute(
                select(StewardProfessionalEvent)
                .where(StewardProfessionalEvent.order_id == order.id)
                .order_by(StewardProfessionalEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return OrderDetailOut(
        **base.model_dump(),
        events=[
            OrderEventOut(
                id=e.id, order_id=e.order_id, action=e.action, actor_admin_id=e.actor_admin_id,
                actor_name=e.actor_name, detail=e.detail, created_at=e.created_at,
            )
            for e in events
        ],
    )


async def _get_order(db: AsyncSession, admin, order_id: int):
    from app.models import StewardProfessionalOrder

    order = await db.get(StewardProfessionalOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    await scope_svc.require_instance_access(db, admin, order.instance_id, audit_cross_scope=False)
    return order


@router.post("/professional/orders/{order_id}/start", response_model=OrderOut)
async def start_professional_order(
    order_id: int,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await prof.start_order(db, admin, await _get_order(db, admin, order_id))
    return await _order_out(db, order)


@router.post("/professional/orders/{order_id}/transfer", response_model=OrderOut)
async def transfer_professional_order(
    order_id: int,
    payload: OrderTransferIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await prof.transfer_order(
        db, admin, await _get_order(db, admin, order_id), payload.to_physician_id, payload.reason
    )
    return await _order_out(db, order)


@router.post("/professional/orders/{order_id}/complete", response_model=OrderOut)
async def complete_professional_order(
    order_id: int,
    payload: OrderCompleteIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await prof.complete_order(
        db, admin, await _get_order(db, admin, order_id), payload.result, payload.result_meta
    )
    return await _order_out(db, order)


@router.post("/professional/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel_professional_order(
    order_id: int,
    payload: ReasonIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await prof.cancel_order(db, admin, await _get_order(db, admin, order_id), payload.reason)
    return await _order_out(db, order)


@router.get("/reports", response_model=list[ReportOut])
async def list_reports(
    instance_id: int | None = Query(default=None),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ReportOut]:
    from app.models import StewardServiceReport

    scope = await scope_svc.load_scope(db, admin)
    stmt = (
        select(StewardServiceReport)
        .where(scope_svc.instance_predicate(scope, StewardServiceReport.instance_id))
        .order_by(StewardServiceReport.id.desc())
    )
    if instance_id:
        stmt = stmt.where(StewardServiceReport.instance_id == instance_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ReportOut(
            id=r.id, instance_id=r.instance_id, version=r.version, report_type=r.report_type,
            title=r.title, content=r.content, data_summary=r.data_summary, basis=r.basis,
            physician_id=r.physician_id, issued_at=r.issued_at, supersedes_id=r.supersedes_id,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/reports", response_model=ReportOut, status_code=201)
async def correct_report(
    payload: ReportCorrectIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> ReportOut:
    instance = await scope_svc.require_instance_access(db, admin, payload.instance_id, audit_cross_scope=False)
    report = await prof.create_correction_report(
        db, admin, instance, payload.title, payload.content, payload.basis
    )
    return ReportOut(
        id=report.id, instance_id=report.instance_id, version=report.version,
        report_type=report.report_type, title=report.title, content=report.content,
        data_summary=report.data_summary, basis=report.basis, physician_id=report.physician_id,
        issued_at=report.issued_at, supersedes_id=report.supersedes_id, created_at=report.created_at,
    )


@router.get("/followups", response_model=list[FollowupRecordOut])
async def list_followups(
    instance_id: int | None = Query(default=None),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[FollowupRecordOut]:
    from app.models import StewardFollowupRecord

    scope = await scope_svc.load_scope(db, admin)
    stmt = (
        select(StewardFollowupRecord)
        .where(scope_svc.instance_predicate(scope, StewardFollowupRecord.instance_id))
        .order_by(StewardFollowupRecord.service_day.asc())
    )
    if instance_id:
        stmt = stmt.where(StewardFollowupRecord.instance_id == instance_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        FollowupRecordOut(
            id=r.id, instance_id=r.instance_id, task_id=r.task_id, service_day=r.service_day,
            summary=r.summary, data_quality_note=r.data_quality_note, next_action=r.next_action,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/followups", response_model=FollowupRecordOut, status_code=201)
async def record_followup(
    payload: FollowupRecordIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> FollowupRecordOut:
    task = await db.get(StewardTask, payload.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="随访任务不存在")
    instance = await scope_svc.require_instance_access(db, admin, task.instance_id, audit_cross_scope=False)
    record = await prof.record_followup(db, admin, instance, payload.task_id, payload.model_dump())
    return FollowupRecordOut(
        id=record.id, instance_id=record.instance_id, task_id=record.task_id,
        service_day=record.service_day, summary=record.summary,
        data_quality_note=record.data_quality_note, next_action=record.next_action,
        created_at=record.created_at,
    )


# ───────────────────── 今日工作台聚合（切片 F） ─────────────────────


@router.get("/workbench")
async def workbench(
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """今日工作台聚合：实例分布、触达待办、超时专业工单、电话工单、设备同步失败、
    放行与条款缺口。只读汇总，明细进各列表页；汇总与列表共用同一范围谓词（F02）。"""
    from datetime import timedelta

    from app.models import (
        DeviceBinding,
        DeviceSyncLog,
        OutreachIntent,
        PhoneCallOrder,
        StewardProfessionalOrder,
        StewardPilotRelease,
        StewardServiceInstance,
    )
    from app.services import steward_service

    scope = await scope_svc.load_scope(db, admin)
    today = steward_service.sh_today()
    instance_rows = (
        await db.execute(
            select(StewardServiceInstance.status, func.count())
            .where(scope_svc.instance_predicate(scope))
            .group_by(StewardServiceInstance.status)
        )
    ).all()
    outreach_rows = (
        await db.execute(
            select(OutreachIntent.status, func.count())
            .where(
                OutreachIntent.service_date == today,
                scope_svc.instance_predicate(scope, OutreachIntent.instance_id),
            )
            .group_by(OutreachIntent.status)
        )
    ).all()
    overdue_prof = int(
        (
            await db.execute(
                select(func.count())
                .select_from(StewardProfessionalOrder)
                .where(
                    StewardProfessionalOrder.status.in_(["pending", "in_progress"]),
                    StewardProfessionalOrder.due_at.is_not(None),
                    StewardProfessionalOrder.due_at < steward_service.now_utc(),
                    scope_svc.instance_predicate(scope, StewardProfessionalOrder.instance_id),
                )
            )
        ).scalar()
        or 0
    )
    high_open = int(
        (
            await db.execute(
                select(func.count())
                .select_from(StewardProfessionalOrder)
                .where(
                    StewardProfessionalOrder.priority == "high",
                    StewardProfessionalOrder.status.in_(["pending", "in_progress"]),
                    StewardProfessionalOrder.transferred_at.is_(None),
                    scope_svc.instance_predicate(scope, StewardProfessionalOrder.instance_id),
                )
            )
        ).scalar()
        or 0
    )
    phone_rows = (
        await db.execute(
            select(PhoneCallOrder.status, func.count())
            .where(scope_svc.instance_predicate(scope, PhoneCallOrder.instance_id))
            .group_by(PhoneCallOrder.status)
        )
    ).all()
    device_failures = int(
        (
            await db.execute(
                select(func.count())
                .select_from(DeviceSyncLog)
                .join(DeviceBinding, DeviceBinding.id == DeviceSyncLog.binding_id)
                .where(
                    DeviceSyncLog.status.in_(["failed", "blocked"]),
                    DeviceSyncLog.started_at >= steward_service.now_utc() - timedelta(days=1),
                    scope_svc.user_predicate(scope, DeviceBinding.user_id),
                )
            )
        ).scalar()
        or 0
    )
    from app.models import StewardScheduleIntent

    schedule_intents_pending = int(
        (
            await db.execute(
                select(func.count())
                .select_from(StewardScheduleIntent)
                .where(
                    StewardScheduleIntent.status == "pending",
                    scope_svc.instance_predicate(scope, StewardScheduleIntent.instance_id),
                )
            )
        ).scalar()
        or 0
    )
    active_releases = int(
        (
            await db.execute(
                select(func.count())
                .select_from(StewardPilotRelease)
                .where(StewardPilotRelease.revoked_at.is_(None))
            )
        ).scalar()
        or 0
    )
    return {
        "date": today.isoformat(),
        "instances": {status: int(count) for status, count in instance_rows},
        "outreach_today": {status: int(count) for status, count in outreach_rows},
        "professional": {"overdue": overdue_prof, "high_priority_open": high_open},
        "phone": {status: int(count) for status, count in phone_rows},
        "schedule_intents_pending": schedule_intents_pending,
        "device_sync_failures_24h": device_failures,
        "pilot_releases_active": active_releases,
    }


# ───────────────────────── 随访预约意向（批次十一） ─────────────────────────


@router.get("/schedule-intents", response_model=list[ScheduleIntentOut])
async def list_schedule_intents(
    status: str | None = Query(default=None, max_length=12),
    instance_id: int | None = Query(default=None),
    admin=Depends(require_permission("steward.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ScheduleIntentOut]:
    from app.models import StewardScheduleIntent

    scope = await scope_svc.load_scope(db, admin)
    stmt = (
        select(StewardScheduleIntent)
        .where(scope_svc.instance_predicate(scope, StewardScheduleIntent.instance_id))
        .order_by(StewardScheduleIntent.id.desc())
    )
    if status:
        stmt = stmt.where(StewardScheduleIntent.status == status)
    if instance_id:
        stmt = stmt.where(StewardScheduleIntent.instance_id == instance_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ScheduleIntentOut(
            id=r.id,
            task_id=r.task_id,
            instance_id=r.instance_id,
            preferred_date=r.preferred_date,
            preferred_period=r.preferred_period,
            note=r.note,
            status=r.status,
            handle_note=r.handle_note,
            handled_at=r.handled_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/schedule-intents/{intent_id}/handle", response_model=ScheduleIntentOut)
async def handle_schedule_intent(
    intent_id: int,
    payload: ScheduleIntentHandleIn,
    admin=Depends(require_permission("steward.manage")),
    db: AsyncSession = Depends(get_db),
) -> ScheduleIntentOut:
    intent = await svc.handle_schedule_intent(db, admin, intent_id, payload.note)
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
