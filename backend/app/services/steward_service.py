"""服务履约域业务层（工作二）：服务实例、激活门禁、45 天日历、暂停补时、结案与联动。

事务约定与工作一相同：跨多表不变量操作在本层内 commit / rollback；路由层只做鉴权、
参数与序列化。并发防护：
- 实例懒创建 UNIQUE(entitlement_id)，插入冲突读回原记录；
- 激活为条件 UPDATE(status='preparing')，并发重复激活只赢一次，输家读回原实例；
- 日历任务 UNIQUE(instance_id, task_key) 兜底，重复生成以 calendar_generated 短路。

时间口径：存储 UTC；业务服务日按 Asia/Shanghai；服务期 = 激活时刻起 service_days×24h
加累计补偿，与权益自然日有效期是两个口径，不混用。
"""

import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    CONSENT_PURPOSE_STEWARD_DEVICE_DATA,
    CONSENT_PURPOSE_STEWARD_SERVICE,
    EntitlementStatus,
    MpEntitlement,
    MpUserConsent,
    PAUSE_COMPENSATION_MIN_SECONDS,
    PREP_CHECK_META,
    PauseCause,
    PrepCheckSource,
    PrepCheckStatus,
    PrepCheckType,
    StewardInstanceStatus,
    StewardPauseRecord,
    StewardPrepCheck,
    StewardServiceInstance,
    StewardStaff,
    StewardTask,
    StewardTaskStatus,
    StewardTimelineEvent,
    User,
    UserStatus,
)
from app.schemas.common import as_utc, mask_phone
from app.services.audit_service import audit

SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc

# 自动门禁项与授权条款配置的对应
_CONSENT_PURPOSE_TEXT_KEY = {
    PrepCheckType.DEVICE_VENDOR_CONSENT.value: "STEWARD_DEVICE_CONSENT_TEXT",
    PrepCheckType.PLATFORM_CONSENT.value: "STEWARD_SERVICE_CONSENT_TEXT",
}
_CONSENT_PURPOSE_MAP = {
    PrepCheckType.DEVICE_VENDOR_CONSENT.value: CONSENT_PURPOSE_STEWARD_DEVICE_DATA,
    PrepCheckType.PLATFORM_CONSENT.value: CONSENT_PURPOSE_STEWARD_SERVICE,
}

_TERMINAL_STATUSES = (
    StewardInstanceStatus.EXPIRED.value,
    StewardInstanceStatus.CLOSED.value,
    StewardInstanceStatus.TERMINATED.value,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def sh_today() -> date:
    return datetime.now(SHANGHAI).date()


def end_of_sh_day(day: date) -> datetime:
    """上海自然日 23:59:59 对应的 UTC 时刻（任务期限口径）。"""
    return datetime.combine(day, time(23, 59, 59), tzinfo=SHANGHAI).astimezone(UTC)


def service_date_for_day(activated_at: datetime, day: int, shift_days: int = 0) -> date:
    base = as_utc(activated_at).astimezone(SHANGHAI).date()
    return base + timedelta(days=day - 1 + shift_days)


def followup_days() -> list[int]:
    raw = settings.STEWARD_FOLLOWUP_DAYS or ""
    days: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value >= 1 and value not in days:
            days.append(value)
    return sorted(days) or [7, 14, 21, 28, 35, 42]


async def add_timeline(
    db: AsyncSession,
    instance: StewardServiceInstance,
    event_type: str,
    *,
    actor_type: str = "system",
    actor_id: int | None = None,
    actor_name: str = "",
    detail: dict | None = None,
) -> None:
    db.add(
        StewardTimelineEvent(
            instance_id=instance.id,
            user_id=instance.user_id,
            event_type=event_type,
            detail=detail,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name or "system",
        )
    )


def user_actor_name(user: User) -> str:
    return mask_phone(user.phone) or (user.nickname or f"用户{user.id}")


# ───────────────────────── 实例生命周期 ─────────────────────────


async def _create_instance_for_entitlement(
    db: AsyncSession, ent: MpEntitlement
) -> StewardServiceInstance:
    """为一份 active 权益创建 preparing 实例与九项门禁行；UNIQUE 兜底幂等。"""
    from app.services.commerce_service import gen_no

    # 续期链：同人同包已有已结束的上一期 → 新实例挂 renewed_from
    renewed_from = (
        await db.execute(
            select(func.max(StewardServiceInstance.id)).where(
                StewardServiceInstance.user_id == ent.user_id,
                StewardServiceInstance.package_id == ent.package_id,
                StewardServiceInstance.status.in_(_TERMINAL_STATUSES),
            )
        )
    ).scalar()
    instance = StewardServiceInstance(
        instance_no=gen_no("SL"),
        entitlement_id=ent.id,
        user_id=ent.user_id,
        package_id=ent.package_id,
        package_version_id=ent.package_version_id,
        package_name=ent.package_name,
        version_no=ent.version_no,
        benefits=ent.benefits or [],
        validity_days=ent.validity_days,
        service_days=settings.STEWARD_SERVICE_DAYS,
        status=StewardInstanceStatus.PREPARING.value,
        renewed_from_instance_id=renewed_from,
    )
    db.add(instance)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(StewardServiceInstance).where(
                    StewardServiceInstance.entitlement_id == ent.id
                )
            )
        ).scalar_one()
        return existing
    for check_type, (_title, source) in PREP_CHECK_META.items():
        db.add(
            StewardPrepCheck(
                instance_id=instance.id, check_type=check_type, source=source.value
            )
        )
    db.add(
        StewardTimelineEvent(
            instance_id=instance.id,
            user_id=instance.user_id,
            event_type="instance_created",
            detail={"entitlement_id": ent.id, "service_days": instance.service_days},
        )
    )
    await db.commit()
    await db.refresh(instance)
    return instance


async def lazy_transition(db: AsyncSession, instance: StewardServiceInstance) -> StewardServiceInstance:
    """读路径懒迁移：权益作废 → terminated；到点 → expired。均条件 UPDATE + 时间线。"""
    if instance.status in (StewardInstanceStatus.CLOSED.value, StewardInstanceStatus.TERMINATED.value):
        return instance
    ent = await db.get(MpEntitlement, instance.entitlement_id)
    changed = False
    if ent is not None and ent.status == EntitlementStatus.VOID.value:
        result = await db.execute(
            update(StewardServiceInstance)
            .where(
                StewardServiceInstance.id == instance.id,
                StewardServiceInstance.status.in_(
                    [
                        StewardInstanceStatus.PREPARING.value,
                        StewardInstanceStatus.ACTIVE.value,
                        StewardInstanceStatus.PAUSED.value,
                    ]
                ),
            )
            .values(
                status=StewardInstanceStatus.TERMINATED.value,
                termination_reason=ent.void_reason or "权益已作废",
                terminated_at=now_utc(),
            )
        )
        if result.rowcount == 1:
            await db.execute(
                update(StewardTask)
                .where(
                    StewardTask.instance_id == instance.id,
                    StewardTask.status == StewardTaskStatus.PENDING.value,
                )
                .values(status=StewardTaskStatus.CANCELLED.value)
            )
            db.add(
                StewardTimelineEvent(
                    instance_id=instance.id,
                    user_id=instance.user_id,
                    event_type="terminated",
                    detail={"reason": ent.void_reason or "权益已作废"},
                )
            )
            await audit(
                db,
                action="steward.instance.terminate",
                resource_type="steward_instance",
                resource_id=instance.id,
                detail={"reason": ent.void_reason or "权益已作废"},
                actor_type="system",
            )
            changed = True
    elif (
        instance.status in (StewardInstanceStatus.ACTIVE.value, StewardInstanceStatus.PAUSED.value)
        and instance.ends_at is not None
        and as_utc(instance.ends_at) <= now_utc()
    ):
        result = await db.execute(
            update(StewardServiceInstance)
            .where(
                StewardServiceInstance.id == instance.id,
                StewardServiceInstance.status.in_(
                    [StewardInstanceStatus.ACTIVE.value, StewardInstanceStatus.PAUSED.value]
                ),
            )
            .values(status=StewardInstanceStatus.EXPIRED.value)
        )
        if result.rowcount == 1:
            db.add(
                StewardTimelineEvent(
                    instance_id=instance.id,
                    user_id=instance.user_id,
                    event_type="expired",
                    detail={"ends_at": as_utc(instance.ends_at).isoformat()},
                )
            )
            await audit(
                db,
                action="steward.instance.expire",
                resource_type="steward_instance",
                resource_id=instance.id,
                detail={},
                actor_type="system",
            )
            changed = True
    if changed:
        await db.commit()
        await db.refresh(instance)
    return instance


async def ensure_instances_for_user(db: AsyncSession, user: User) -> list[StewardServiceInstance]:
    """本人全部权益对应的服务实例（含已终止 / 已结案的服务史）；active 权益懒创建，
    非 active 权益只在实例已存在时展示（pending 权益尚未进入履约）。"""
    if user.status == UserStatus.BANNED:
        raise HTTPException(status_code=401, detail="账号已被封禁")
    entitlements = (
        await db.execute(
            select(MpEntitlement)
            .where(MpEntitlement.user_id == user.id)
            .order_by(MpEntitlement.id.asc())
        )
    ).scalars().all()
    instances: list[StewardServiceInstance] = []
    for ent in entitlements:
        instance = (
            await db.execute(
                select(StewardServiceInstance).where(
                    StewardServiceInstance.entitlement_id == ent.id
                )
            )
        ).scalar_one_or_none()
        if instance is None:
            if ent.status != EntitlementStatus.ACTIVE.value:
                continue
            instance = await _create_instance_for_entitlement(db, ent)
        instances.append(await lazy_transition(db, instance))
    return instances


async def instance_for_user(
    db: AsyncSession, user: User, instance_id: int
) -> StewardServiceInstance:
    """C 端归属校验：跨用户一律 404，不泄露存在性。"""
    instance = await db.get(StewardServiceInstance, instance_id)
    if instance is None or instance.user_id != user.id:
        raise HTTPException(status_code=404, detail="服务不存在")
    return await lazy_transition(db, instance)


async def instance_for_admin(db: AsyncSession, instance_id: int) -> StewardServiceInstance:
    instance = await db.get(StewardServiceInstance, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="服务实例不存在")
    return await lazy_transition(db, instance)


# ───────────────────────── 激活门禁 ─────────────────────────


async def _has_unrevoked_consent(db: AsyncSession, user_id: int, purpose: str) -> bool:
    exists = (
        await db.execute(
            select(MpUserConsent.id)
            .where(
                MpUserConsent.user_id == user_id,
                MpUserConsent.purpose == purpose,
                MpUserConsent.revoked_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return exists is not None


async def _auto_check_states(
    db: AsyncSession, instance: StewardServiceInstance, user: User, ent: MpEntitlement
) -> dict[str, str | None]:
    """返回 {check_type: 通过(None) 或 未通过原因}；只覆盖 auto 项。"""
    states: dict[str, str | None] = {}

    ok = bool(user.phone) and ent.user_id == instance.user_id
    states[PrepCheckType.IDENTITY_PHONE.value] = (
        None if ok else "微信手机号未核验或权益归属与本人不一致"
    )

    ent_ok = ent.status == EntitlementStatus.ACTIVE.value and (
        ent.valid_until is None or as_utc(ent.valid_until) >= now_utc()
    )
    states[PrepCheckType.ENTITLEMENT_VALID.value] = None if ent_ok else "权益已失效、过期或作废"

    for check_type, purpose in _CONSENT_PURPOSE_MAP.items():
        text = getattr(settings, _CONSENT_PURPOSE_TEXT_KEY[check_type], "") or ""
        if not text.strip():
            states[check_type] = "授权条款待配置（运营定稿后开放）"
        elif await _has_unrevoked_consent(db, instance.user_id, purpose):
            states[check_type] = None
        else:
            states[check_type] = "尚未完成该授权"

    staffing_missing = []
    for field, role_label in (
        ("butler_id", "负责管家"),
        ("backup_butler_id", "替补管家"),
        ("physician_id", "签约医师"),
    ):
        staff_id = getattr(instance, field)
        if staff_id is None:
            staffing_missing.append(f"{role_label}未指派")
            continue
        staff = await db.get(StewardStaff, staff_id)
        if staff is None or staff.status != "active":
            staffing_missing.append(f"{role_label}不可用")
    states[PrepCheckType.STAFFING_READY.value] = (
        None if not staffing_missing else "；".join(staffing_missing)
    )
    return states


async def evaluate_prep_checks(
    db: AsyncSession, instance: StewardServiceInstance
) -> list[StewardPrepCheck]:
    """实时判定 auto 项并回写快照；manual 项保持后台录入结果。返回九项（契约顺序）。"""
    user = await db.get(User, instance.user_id)
    ent = await db.get(MpEntitlement, instance.entitlement_id)
    rows = (
        await db.execute(
            select(StewardPrepCheck).where(StewardPrepCheck.instance_id == instance.id)
        )
    ).scalars().all()
    by_type = {row.check_type: row for row in rows}
    if user is not None and ent is not None:
        auto_states = await _auto_check_states(db, instance, user, ent)
    else:
        auto_states = {}
    changed = False
    for check_type, (title, source) in PREP_CHECK_META.items():
        row = by_type.get(check_type)
        if row is None:
            row = StewardPrepCheck(instance_id=instance.id, check_type=check_type, source=source.value)
            db.add(row)
            by_type[check_type] = row
            changed = True
        if source != PrepCheckSource.AUTO or check_type not in auto_states:
            continue  # manual 项状态只来自后台录入，读取路径不改写
        reason = auto_states[check_type]
        new_status = PrepCheckStatus.PASSED.value if reason is None else PrepCheckStatus.PENDING.value
        if row.status != new_status or row.missing_reason != reason:
            row.status = new_status
            row.missing_reason = reason
            if reason is not None:
                # auto 项回退时清掉痕迹（auto 项本不允许人工置过，防御性清理）
                row.evidence = None
                row.completed_at = None
                row.completed_by_admin_id = None
            else:
                row.completed_at = row.completed_at or now_utc()
            changed = True
    if changed:
        await db.commit()
        await db.refresh(instance)
    return [by_type[key] for key in PREP_CHECK_META if key in by_type]


def prep_missing(checks: list[StewardPrepCheck]) -> list[str]:
    return [row.check_type for row in checks if row.status != PrepCheckStatus.PASSED.value]


# ───────────────────────── 激活 ─────────────────────────


async def activate_instance(
    db: AsyncSession, user: User, instance: StewardServiceInstance
) -> StewardServiceInstance:
    """本人确认激活：九项门禁全过才转 active 并生成日历；幂等返回原实例。"""
    instance = await lazy_transition(db, instance)
    if instance.status == StewardInstanceStatus.TERMINATED.value:
        raise HTTPException(status_code=409, detail="该服务已终止，无法激活")
    if instance.status != StewardInstanceStatus.PREPARING.value:
        return instance  # 已激活 / 到期 / 结案：幂等返回原实例

    checks = await evaluate_prep_checks(db, instance)
    missing = prep_missing(checks)
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"message": "激活条件未全部满足", "missing": missing},
        )

    activated = now_utc()
    ends_at = activated + timedelta(days=instance.service_days)
    result = await db.execute(
        update(StewardServiceInstance)
        .where(
            StewardServiceInstance.id == instance.id,
            StewardServiceInstance.status == StewardInstanceStatus.PREPARING.value,
        )
        .values(
            status=StewardInstanceStatus.ACTIVE.value,
            activated_at=activated,
            ends_at=ends_at,
            original_ends_at=ends_at,
        )
    )
    if result.rowcount != 1:
        # 并发重复激活：另一请求已成功，读回原实例（无未提交变更，不 rollback）
        await db.refresh(instance)
        if instance.status == StewardInstanceStatus.ACTIVE.value:
            return instance
        raise HTTPException(status_code=409, detail="激活状态冲突，请刷新后查看")

    await generate_calendar(db, instance)
    await add_timeline(
        db,
        instance,
        "activated",
        actor_type="user",
        actor_id=user.id,
        actor_name=user_actor_name(user),
        detail={
            "activated_at": activated.isoformat(),
            "ends_at": ends_at.isoformat(),
            "service_days": instance.service_days,
        },
    )
    await audit(
        db,
        action="steward.instance.activate",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail={"service_days": instance.service_days},
        actor_type="user",
        actor_id=user.id,
        actor_name=user_actor_name(user),
    )
    await db.commit()
    await db.refresh(instance)
    return instance


# ───────────────────────── 日历与任务 ─────────────────────────


def build_task_specs(instance: StewardServiceInstance) -> list[dict]:
    """按契约生成任务规格：每日事项 ×service_days、随访节点、复评与结案。"""
    activated = as_utc(instance.activated_at)
    service_days = instance.service_days

    def spec(day: int, task_type: str, title: str, assignee: str, **extra) -> dict:
        service_date = service_date_for_day(activated, day)
        return {
            "task_key": f"d{day}:{task_type}",
            "service_day": day,
            "service_date": service_date,
            "task_type": task_type,
            "title": title,
            "assignee_type": assignee,
            "due_at": end_of_sh_day(service_date),
            **extra,
        }

    specs = [
        spec(day, "daily", f"第 {day} 天 · 日常记录与计划执行", "user")
        for day in range(1, service_days + 1)
    ]
    for fd in followup_days():
        if fd <= service_days:
            specs.append(
                spec(fd, "follow_up", f"第 {fd} 天 · 管家随访", "butler", butler_id=instance.butler_id)
            )
    if service_days >= 3:
        review_day = service_days - 2
        specs.append(
            spec(
                review_day,
                "review_prep",
                f"第 {review_day} 天 · 复评资料准备",
                "butler",
                butler_id=instance.butler_id,
            )
        )
        specs.append(
            spec(
                review_day + 1,
                "final_review",
                f"第 {review_day + 1} 天 · 医师期末复评",
                "physician",
                physician_id=instance.physician_id,
            )
        )
    specs.append(
        spec(
            service_days,
            "closure",
            f"第 {service_days} 天 · 结案与报告交付",
            "butler",
            butler_id=instance.butler_id,
        )
    )
    return specs


async def generate_calendar(db: AsyncSession, instance: StewardServiceInstance) -> int:
    """幂等生成日历任务。先查既有任务数（数据一致性自愈：有任务即补 flag 返回），
    再插入；插入冲突属异常状态，显式抛错让整个激活事务回滚，绝不半提交。"""
    await db.refresh(instance)
    if instance.calendar_generated:
        return 0
    existing = int(
        (
            await db.execute(
                select(func.count()).select_from(StewardTask).where(
                    StewardTask.instance_id == instance.id
                )
            )
        ).scalar()
        or 0
    )
    if existing > 0:
        instance.calendar_generated = True
        await db.flush()
        return 0
    created = 0
    for item in build_task_specs(instance):
        db.add(
            StewardTask(
                instance_id=instance.id,
                user_id=instance.user_id,
                task_key=item["task_key"],
                service_day=item["service_day"],
                service_date=item["service_date"],
                task_type=item["task_type"],
                title=item["title"],
                assignee_type=item["assignee_type"],
                butler_id=item.get("butler_id"),
                physician_id=item.get("physician_id"),
                due_at=item["due_at"],
                plan_version=instance.plan_version,
            )
        )
        created += 1
    instance.calendar_generated = True
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="日历任务生成冲突，请重试")
    return created


async def current_service_day(db: AsyncSession, instance: StewardServiceInstance) -> int | None:
    if instance.activated_at is None:
        return None
    today = sh_today()
    row = (
        await db.execute(
            select(StewardTask.service_day)
            .where(
                StewardTask.instance_id == instance.id,
                StewardTask.task_type == "daily",
                StewardTask.service_date <= today,
            )
            .order_by(StewardTask.service_day.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    return 1


async def tasks_on(db: AsyncSession, instance: StewardServiceInstance, day: date) -> list[StewardTask]:
    return list(
        (
            await db.execute(
                select(StewardTask)
                .where(StewardTask.instance_id == instance.id, StewardTask.service_date == day)
                .order_by(StewardTask.task_type.desc(), StewardTask.service_day.asc())
            )
        )
        .scalars()
        .all()
    )


async def calendar_month(
    db: AsyncSession, instance: StewardServiceInstance, month_first: date
) -> list[dict]:
    """月视图：该月内出现的任务按业务日聚合；无任务的日子也出现在范围内时留空。"""
    month_last = (month_first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    result = await db.execute(
        select(StewardTask)
        .where(
            StewardTask.instance_id == instance.id,
            StewardTask.service_date >= month_first,
            StewardTask.service_date <= month_last,
        )
        .order_by(StewardTask.service_date.asc(), StewardTask.service_day.asc())
    )
    rows = result.scalars().all()
    by_date: dict[date, list[StewardTask]] = {}
    for task in rows:
        by_date.setdefault(task.service_date, []).append(task)
    days = []
    day_iter = month_first
    while day_iter <= month_last:
        day_tasks = by_date.get(day_iter, [])
        days.append(
            {
                "date": day_iter,
                "service_day": day_tasks[0].service_day if day_tasks else None,
                "tasks": day_tasks,
            }
        )
        day_iter += timedelta(days=1)
    return days


async def complete_user_task(
    db: AsyncSession, user: User, instance: StewardServiceInstance, task_id: int, result: str | None
) -> StewardTask:
    task = await db.get(StewardTask, task_id)
    if task is None or task.instance_id != instance.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.assignee_type != "user":
        raise HTTPException(status_code=403, detail="该任务由服务方完成，请勿自行操作")
    if task.status != StewardTaskStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="该任务已完成或已取消")
    task.status = StewardTaskStatus.DONE.value
    task.result = result
    task.completed_at = now_utc()
    await add_timeline(
        db,
        instance,
        "task_completed",
        actor_type="user",
        actor_id=user.id,
        actor_name=user_actor_name(user),
        detail={"task_id": task.id, "task_type": task.task_type, "service_day": task.service_day},
    )
    await db.commit()
    await db.refresh(task)
    return task


async def record_task_result(
    db: AsyncSession, admin, task_id: int, result: str
) -> StewardTask:
    task = await db.get(StewardTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != StewardTaskStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="该任务已有结果")
    task.status = StewardTaskStatus.DONE.value
    task.result = result
    task.completed_at = now_utc()
    task.completed_by_admin_id = admin.id
    instance = await db.get(StewardServiceInstance, task.instance_id)
    if instance is not None:
        await add_timeline(
            db,
            instance,
            "task_completed",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.name,
            detail={"task_id": task.id, "task_type": task.task_type, "service_day": task.service_day},
        )
    await audit(
        db,
        action="steward.task.result",
        resource_type="steward_task",
        resource_id=task.id,
        detail={"service_day": task.service_day, "task_type": task.task_type},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(task)
    return task


# ───────────────────────── 门禁录入 / 指派 ─────────────────────────


async def pass_prep_check(
    db: AsyncSession, admin, instance: StewardServiceInstance, check_type: str, evidence: str
) -> StewardPrepCheck:
    row = (
        await db.execute(
            select(StewardPrepCheck).where(
                StewardPrepCheck.instance_id == instance.id,
                StewardPrepCheck.check_type == check_type,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="检查项不存在")
    if row.source != PrepCheckSource.MANUAL.value:
        raise HTTPException(
            status_code=409, detail="该项由服务端自动判定，不能人工置通过"
        )
    if row.status == PrepCheckStatus.PASSED.value:
        raise HTTPException(status_code=409, detail="该项已通过")
    row.status = PrepCheckStatus.PASSED.value
    row.evidence = evidence
    row.completed_at = now_utc()
    row.completed_by_admin_id = admin.id
    await add_timeline(
        db,
        instance,
        "prep_check_passed",
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        detail={"check_type": check_type, "evidence": evidence[:200]},
    )
    await audit(
        db,
        action="steward.prep.pass",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail={"check_type": check_type},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(row)
    return row


async def reset_prep_check(
    db: AsyncSession, admin, instance: StewardServiceInstance, check_type: str, reason: str
) -> StewardPrepCheck:
    row = (
        await db.execute(
            select(StewardPrepCheck).where(
                StewardPrepCheck.instance_id == instance.id,
                StewardPrepCheck.check_type == check_type,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.source != PrepCheckSource.MANUAL.value:
        raise HTTPException(status_code=404, detail="人工检查项不存在")
    if row.status != PrepCheckStatus.PASSED.value:
        raise HTTPException(status_code=409, detail="该项尚未通过")
    row.status = PrepCheckStatus.PENDING.value
    row.missing_reason = f"人工录入已回退：{reason}"
    row.evidence = None
    row.completed_at = None
    row.completed_by_admin_id = None
    await add_timeline(
        db,
        instance,
        "prep_check_reset",
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        detail={"check_type": check_type, "reason": reason},
    )
    await audit(
        db,
        action="steward.prep.reset",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail={"check_type": check_type, "reason": reason},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(row)
    return row


async def assign_staff(
    db: AsyncSession, admin, instance: StewardServiceInstance, payload: dict
) -> StewardServiceInstance:
    role_by_field = {"butler_id": "butler", "backup_butler_id": "butler", "physician_id": "physician"}
    changes: dict[str, int | None] = {}
    for field, role in role_by_field.items():
        if field not in payload:
            continue
        value = payload[field]
        if value is not None:
            staff = await db.get(StewardStaff, value)
            if staff is None:
                raise HTTPException(status_code=404, detail=f"人员不存在：{value}")
            if staff.role != role:
                raise HTTPException(status_code=422, detail=f"人员 {staff.name} 角色不匹配（需要 {role}）")
            if staff.status != "active":
                raise HTTPException(status_code=409, detail=f"人员 {staff.name} 已停用")
        changes[field] = value
    if not changes:
        raise HTTPException(status_code=422, detail="未提供任何指派变更")
    for field, value in changes.items():
        setattr(instance, field, value)
    await add_timeline(
        db,
        instance,
        "staff_assigned",
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        detail={k: v for k, v in changes.items()},
    )
    await audit(
        db,
        action="steward.assign",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail=changes,
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(instance)
    return instance


# ───────────────────────── 暂停 / 补时 / 结案 ─────────────────────────


async def pause_instance(
    db: AsyncSession, admin, instance: StewardServiceInstance, cause: str, reason: str
) -> StewardServiceInstance:
    instance = await lazy_transition(db, instance)
    if instance.status != StewardInstanceStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail="仅服务中的实例可以暂停")
    result = await db.execute(
        update(StewardServiceInstance)
        .where(
            StewardServiceInstance.id == instance.id,
            StewardServiceInstance.status == StewardInstanceStatus.ACTIVE.value,
        )
        .values(status=StewardInstanceStatus.PAUSED.value)
    )
    if result.rowcount != 1:
        await db.refresh(instance)
        raise HTTPException(status_code=409, detail="暂停状态冲突，请刷新后重试")
    db.add(
        StewardPauseRecord(
            instance_id=instance.id,
            cause=cause,
            reason=reason,
            started_at=now_utc(),
            created_by_admin_id=admin.id,
        )
    )
    await add_timeline(
        db,
        instance,
        "paused",
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        detail={"cause": cause, "reason": reason},
    )
    await audit(
        db,
        action="steward.pause",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail={"cause": cause, "reason": reason},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(instance)
    return instance


async def resume_instance(
    db: AsyncSession, admin, instance: StewardServiceInstance, note: str | None
) -> StewardServiceInstance:
    if instance.status != StewardInstanceStatus.PAUSED.value:
        raise HTTPException(status_code=409, detail="仅已暂停的实例可以恢复")
    record = (
        await db.execute(
            select(StewardPauseRecord)
            .where(
                StewardPauseRecord.instance_id == instance.id,
                StewardPauseRecord.ended_at.is_(None),
            )
            .order_by(StewardPauseRecord.id.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=409, detail="未找到进行中的暂停记录")
    result = await db.execute(
        update(StewardServiceInstance)
        .where(
            StewardServiceInstance.id == instance.id,
            StewardServiceInstance.status == StewardInstanceStatus.PAUSED.value,
        )
        .values(status=StewardInstanceStatus.ACTIVE.value)
    )
    if result.rowcount != 1:
        await db.refresh(instance)
        raise HTTPException(status_code=409, detail="恢复状态冲突，请刷新后重试")

    ended = now_utc()
    duration = int((ended - as_utc(record.started_at)).total_seconds())
    # 补时规则：医师建议暂停全额补时；服务方核心中断超过 24h 全额补时，不足仅记录
    if record.cause == PauseCause.PHYSICIAN_ADVISED.value:
        compensated = duration
    elif (
        record.cause == PauseCause.PROVIDER_INTERRUPTION.value
        and duration > PAUSE_COMPENSATION_MIN_SECONDS
    ):
        compensated = duration
    else:
        compensated = 0
    record.ended_at = ended
    record.duration_seconds = duration
    record.compensated_seconds = compensated
    record.note = note

    shift_days = 0
    if compensated > 0:
        shift_days = math.ceil(compensated / 86400)
        instance.ends_at = as_utc(instance.ends_at) + timedelta(seconds=compensated)
        instance.compensated_seconds = (instance.compensated_seconds or 0) + compensated
        today = sh_today()
        future_tasks = (
            await db.execute(
                select(StewardTask).where(
                    StewardTask.instance_id == instance.id,
                    StewardTask.status == StewardTaskStatus.PENDING.value,
                    StewardTask.service_date >= today,
                )
            )
        ).scalars().all()
        for task in future_tasks:
            new_date = task.service_date + timedelta(days=shift_days)
            task.service_date = new_date
            task.due_at = end_of_sh_day(new_date)
            task.shifted_days = (task.shifted_days or 0) + shift_days
        record.tasks_shifted_days = shift_days
        await add_timeline(
            db,
            instance,
            "compensated",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.name,
            detail={
                "compensated_seconds": compensated,
                "shift_days": shift_days,
                "shifted_tasks": len(future_tasks),
                "new_ends_at": as_utc(instance.ends_at).isoformat(),
            },
        )
    await add_timeline(
        db,
        instance,
        "resumed",
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        detail={"note": note, "compensated_seconds": compensated},
    )
    await audit(
        db,
        action="steward.resume",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail={"compensated_seconds": compensated, "shift_days": shift_days},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(instance)
    return instance


async def close_instance(
    db: AsyncSession, admin, instance: StewardServiceInstance, closure_report: str
) -> StewardServiceInstance:
    instance = await lazy_transition(db, instance)
    if instance.status != StewardInstanceStatus.EXPIRED.value:
        raise HTTPException(status_code=409, detail="服务到期后才能结案")
    final_review = (
        await db.execute(
            select(StewardTask).where(
                StewardTask.instance_id == instance.id,
                StewardTask.task_type == "final_review",
            )
        )
    ).scalar_one_or_none()
    if final_review is None or final_review.status != StewardTaskStatus.DONE.value:
        raise HTTPException(status_code=409, detail="期末复评未完成，不能结案")
    # 切片 D 接入后：此处追加「未结高优先级医师工单必须已转交」检查
    result = await db.execute(
        update(StewardServiceInstance)
        .where(
            StewardServiceInstance.id == instance.id,
            StewardServiceInstance.status == StewardInstanceStatus.EXPIRED.value,
        )
        .values(
            status=StewardInstanceStatus.CLOSED.value,
            closed_at=now_utc(),
            closure_report=closure_report,
        )
    )
    if result.rowcount != 1:
        await db.refresh(instance)
        raise HTTPException(status_code=409, detail="结案状态冲突，请刷新后重试")
    await add_timeline(
        db,
        instance,
        "closed",
        actor_type="admin",
        actor_id=admin.id,
        actor_name=admin.name,
        detail={"closure_report": closure_report[:200]},
    )
    await audit(
        db,
        action="steward.close",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail={},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(instance)
    return instance


# ───────────────────────── 授权 ─────────────────────────


_CONSENT_TEXT = {
    CONSENT_PURPOSE_STEWARD_SERVICE: lambda: settings.STEWARD_SERVICE_CONSENT_TEXT,
    CONSENT_PURPOSE_STEWARD_DEVICE_DATA: lambda: settings.STEWARD_DEVICE_CONSENT_TEXT,
}


async def grant_steward_consent(db: AsyncSession, user: User, purpose: str) -> MpUserConsent:
    """授予履约授权。条款文本来自服务端配置；未配置时 503 待配置，不接受客户端自备文本。"""
    getter = _CONSENT_TEXT.get(purpose)
    text = (getter() if getter is not None else "") or ""
    if not text.strip():
        raise HTTPException(status_code=503, detail="该授权条款尚未配置，暂不能授权")
    existing = (
        await db.execute(
            select(MpUserConsent)
            .where(
                MpUserConsent.user_id == user.id,
                MpUserConsent.purpose == purpose,
                MpUserConsent.revoked_at.is_(None),
            )
            .order_by(MpUserConsent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    consent = MpUserConsent(user_id=user.id, purpose=purpose, version="v1", text=text)
    db.add(consent)
    await audit(
        db,
        action="steward.consent.grant",
        resource_type="mp_user_consent",
        detail={"purpose": purpose},
        actor_type="user",
        actor_id=user.id,
        actor_name=user_actor_name(user),
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(MpUserConsent)
                .where(
                    MpUserConsent.user_id == user.id,
                    MpUserConsent.purpose == purpose,
                    MpUserConsent.revoked_at.is_(None),
                )
                .limit(1)
            )
        ).scalar_one()
        return existing
    await db.refresh(consent)
    return consent


# ───────────────────────── 履约人员 ─────────────────────────


async def create_staff(db: AsyncSession, admin, payload: dict) -> StewardStaff:
    staff = StewardStaff(**payload)
    db.add(staff)
    await audit(
        db,
        action="steward.staff.create",
        resource_type="steward_staff",
        detail={"name": payload.get("name"), "role": payload.get("role")},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(staff)
    return staff


async def update_staff(db: AsyncSession, admin, staff: StewardStaff, payload: dict) -> StewardStaff:
    for field, value in payload.items():
        setattr(staff, field, value)
    await audit(
        db,
        action="steward.staff.update",
        resource_type="steward_staff",
        resource_id=staff.id,
        detail=payload,
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(staff)
    return staff
