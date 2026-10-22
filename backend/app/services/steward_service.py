"""服务履约域业务层（工作二）：服务实例、激活门禁、45 天日历、暂停补时、结案与联动。

事务约定与工作一相同：跨多表不变量操作在本层内 commit / rollback；路由层只做鉴权、
参数与序列化。并发防护：
- 实例懒创建 UNIQUE(entitlement_id)，插入冲突读回原记录；
- 激活为条件 UPDATE(status='preparing')，并发重复激活只赢一次，输家读回原实例；
- 日历任务 UNIQUE(instance_id, task_key) 兜底，重复生成以 calendar_generated 短路。

时间口径：存储 UTC；业务服务日按 Asia/Shanghai；服务期 = 激活时刻起 service_days×24h
加累计补偿，与权益自然日有效期是两个口径，不混用。

批次一（S1/S2/A1–A3，2026-10-22）要点：
- 授权以条款表当前版本为准（先展示后确认、版本一致校验、分用途撤回联动）；
- 激活第十项门禁 pilot_release：无有效放行记录一律阻断（默认未放行）；
- 激活同事务写激活凭证快照；physician_plan 门禁按签发计划自动判定；
- 暂停期间计时停止（paused 不按 ends_at 过期）；补偿边界 = 严格大于 24h；
- lifecycle_sweep 周期推进到期 / 作废，无人读页面也能及时停止后续任务。
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
    CONSENT_PURPOSE_STEWARD_CALL_RECORDING,
    CONSENT_PURPOSE_STEWARD_CONTACT_OUTREACH,
    PhoneCallOrder,
    CONSENT_PURPOSE_STEWARD_DEVICE_DATA,
    CONSENT_PURPOSE_STEWARD_PHONE_OUTREACH,
    CONSENT_PURPOSE_STEWARD_SERVICE,
    EntitlementStatus,
    MpEntitlement,
    MpUserConsent,
    PAUSE_COMPENSATION_MIN_SECONDS,
    PILOT_RELEASE_EVIDENCE_ITEMS,
    PREP_CHECK_META,
    PauseCause,
    PrepCheckSource,
    PrepCheckStatus,
    PrepCheckType,
    StewardActivationCredential,
    StewardCarePlan,
    StewardConsentTerms,
    StewardInstanceStatus,
    StewardPauseRecord,
    StewardPilotRelease,
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

# 自动门禁项与授权 purpose 的对应；条款以 steward_consent_terms 当前 active 版本为准
STEWARD_CONSENT_PURPOSES = (
    CONSENT_PURPOSE_STEWARD_SERVICE,
    CONSENT_PURPOSE_STEWARD_DEVICE_DATA,
    CONSENT_PURPOSE_STEWARD_PHONE_OUTREACH,
    CONSENT_PURPOSE_STEWARD_CONTACT_OUTREACH,
    CONSENT_PURPOSE_STEWARD_CALL_RECORDING,
)
_CONSENT_PURPOSE_MAP = {
    PrepCheckType.DEVICE_VENDOR_CONSENT.value: CONSENT_PURPOSE_STEWARD_DEVICE_DATA,
    PrepCheckType.PLATFORM_CONSENT.value: CONSENT_PURPOSE_STEWARD_SERVICE,
}

# 仍在服务中的状态（终止条件 / 撤回联动 / 扫描共用）
_ACTIVE_LIKE_STATUSES = (
    StewardInstanceStatus.PREPARING.value,
    StewardInstanceStatus.ACTIVE.value,
    StewardInstanceStatus.PAUSED.value,
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
    """为一份 active 权益创建 preparing 实例与九项门禁行；UNIQUE 兜底幂等。

    renewed_from_instance_id 不做自动推断：普通再次购买与显式续期是两个业务事实，
    续期链仅由后续显式续期动作写入（A3 复核修正，防误标）。
    """
    from app.services.commerce_service import gen_no

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
    """读路径懒迁移：权益作废 → terminated；到点 → expired。均条件 UPDATE + 时间线。

    A2 修正：paused 实例不按 ends_at 判到期——暂停期间服务计时已停止，
    其出路只有恢复（结算补偿）或终止；恢复后不补时的场景再按 active 规则正常过期。
    """
    if instance.status in (StewardInstanceStatus.CLOSED.value, StewardInstanceStatus.TERMINATED.value):
        return instance
    ent = await db.get(MpEntitlement, instance.entitlement_id)
    changed = False
    if ent is not None and ent.status == EntitlementStatus.VOID.value:
        await _terminate_instance(db, instance, ent.void_reason or "权益已作废")
        changed = True
    elif (
        instance.status == StewardInstanceStatus.ACTIVE.value
        and instance.ends_at is not None
        and as_utc(instance.ends_at) <= now_utc()
    ):
        await _expire_instance(db, instance)
        changed = True
    if changed:
        await db.commit()
        await db.refresh(instance)
    return instance


async def _terminate_instance(
    db: AsyncSession, instance: StewardServiceInstance, reason: str
) -> bool:
    """终止实例并取消待办任务；条件 UPDATE 保证幂等（读路径与 sweep 共用）。"""
    result = await db.execute(
        update(StewardServiceInstance)
        .where(
            StewardServiceInstance.id == instance.id,
            StewardServiceInstance.status.in_(_ACTIVE_LIKE_STATUSES),
        )
        .values(
            status=StewardInstanceStatus.TERMINATED.value,
            termination_reason=reason,
            terminated_at=now_utc(),
        )
    )
    if result.rowcount != 1:
        return False
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
            detail={"reason": reason},
        )
    )
    await audit(
        db,
        action="steward.instance.terminate",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail={"reason": reason},
        actor_type="system",
    )
    # 即时停止该实例触达意图（sweep 兜底；延迟导入避免循环依赖）
    from app.services import outreach_service

    await outreach_service.stop_for_instance(db, instance.id, "instance_terminated")
    return True


async def _expire_instance(db: AsyncSession, instance: StewardServiceInstance) -> bool:
    """到期迁移（仅 active）；条件 UPDATE 保证幂等（读路径与 sweep 共用）。"""
    result = await db.execute(
        update(StewardServiceInstance)
        .where(
            StewardServiceInstance.id == instance.id,
            StewardServiceInstance.status == StewardInstanceStatus.ACTIVE.value,
        )
        .values(status=StewardInstanceStatus.EXPIRED.value)
    )
    if result.rowcount != 1:
        return False
    db.add(
        StewardTimelineEvent(
            instance_id=instance.id,
            user_id=instance.user_id,
            event_type="expired",
            detail={
                "ends_at": as_utc(instance.ends_at).isoformat()
                if instance.ends_at
                else None
            },
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
    return True


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


async def current_terms(db: AsyncSession, purpose: str) -> StewardConsentTerms | None:
    """某 purpose 当前唯一 active 条款版本；无则 None（待配置）。"""
    return (
        await db.execute(
            select(StewardConsentTerms)
            .where(
                StewardConsentTerms.purpose == purpose,
                StewardConsentTerms.status == "active",
            )
            .order_by(StewardConsentTerms.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _matched_consent(
    db: AsyncSession, user_id: int, purpose: str, version: str
) -> MpUserConsent | None:
    """未撤回且版本一致的授权记录（S1：旧版本授权不满足当前条款）。"""
    return (
        await db.execute(
            select(MpUserConsent)
            .where(
                MpUserConsent.user_id == user_id,
                MpUserConsent.purpose == purpose,
                MpUserConsent.version == version,
                MpUserConsent.revoked_at.is_(None),
            )
            .order_by(MpUserConsent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def valid_care_plan(
    db: AsyncSession, user_id: int, physician_id: int | None
) -> StewardCarePlan | None:
    """本人最新有效签发计划；physician_id 给出时须与签发医师一致。"""
    stmt = (
        select(StewardCarePlan)
        .where(
            StewardCarePlan.user_id == user_id,
            StewardCarePlan.revoked_at.is_(None),
            (StewardCarePlan.valid_until.is_(None)) | (StewardCarePlan.valid_until >= now_utc()),
        )
        .order_by(StewardCarePlan.id.desc())
        .limit(1)
    )
    plan = (await db.execute(stmt)).scalar_one_or_none()
    if plan is None:
        return None
    if physician_id is not None and plan.physician_id != physician_id:
        return None
    return plan


async def _auto_check_states(
    db: AsyncSession, instance: StewardServiceInstance, user: User, ent: MpEntitlement
) -> dict[str, str | None]:
    """返回 {check_type: 通过(None) 或 未通过原因}；只覆盖 auto 项。"""
    states: dict[str, str | None] = {}

    # 身份：手机号须经微信身份核验（wx_openid 存在 = 微信账号体系内的本人），非自填号码
    ok = (
        bool(user.phone)
        and bool(user.wx_openid)
        and user.status == UserStatus.ACTIVE.value
        and ent.user_id == instance.user_id
    )
    states[PrepCheckType.IDENTITY_PHONE.value] = (
        None if ok else "微信手机号未核验或权益归属与本人不一致"
    )

    ent_ok = ent.status == EntitlementStatus.ACTIVE.value and (
        ent.valid_until is None or as_utc(ent.valid_until) >= now_utc()
    )
    states[PrepCheckType.ENTITLEMENT_VALID.value] = None if ent_ok else "权益已失效、过期或作废"

    for check_type, purpose in _CONSENT_PURPOSE_MAP.items():
        terms = await current_terms(db, purpose)
        if terms is None:
            states[check_type] = "授权条款待配置（运营定稿后开放）"
            continue
        matched = await _matched_consent(db, instance.user_id, purpose, terms.version)
        if matched is not None:
            states[check_type] = None
        elif await _has_any_unrevoked_consent(db, instance.user_id, purpose):
            states[check_type] = "条款已更新，请查看最新条款并重新确认"
        else:
            states[check_type] = "尚未完成该授权"

    # 设备门禁（切片 C 起自动判定）：active 绑定 + 有效数据
    from app.services import device_service

    if await device_service.active_binding_for_user(db, instance.user_id) is None:
        states[PrepCheckType.DEVICE_PAIRED.value] = "尚未绑定设备（或绑定已解绑）"
        states[PrepCheckType.FIRST_VALID_DATA.value] = "尚未绑定设备，无有效数据"
    elif await device_service.has_valid_data(db, instance.user_id):
        states[PrepCheckType.DEVICE_PAIRED.value] = None
        states[PrepCheckType.FIRST_VALID_DATA.value] = None
    else:
        states[PrepCheckType.DEVICE_PAIRED.value] = None
        states[PrepCheckType.FIRST_VALID_DATA.value] = "尚无本人有效设备数据（须厂商侧实际同步）"

    plan = await valid_care_plan(db, instance.user_id, instance.physician_id)
    if plan is not None:
        states[PrepCheckType.PHYSICIAN_PLAN.value] = None
    elif instance.physician_id is None:
        states[PrepCheckType.PHYSICIAN_PLAN.value] = "未指派签约医师，无法核验签发计划"
    else:
        states[PrepCheckType.PHYSICIAN_PLAN.value] = "医师初评签发计划缺失、已撤销或与签约医师不一致"

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
    if instance.butler_id is not None and instance.butler_id == instance.backup_butler_id:
        staffing_missing.append("负责管家与替补不能为同一人")
    states[PrepCheckType.STAFFING_READY.value] = (
        None if not staffing_missing else "；".join(staffing_missing)
    )
    return states


async def _has_any_unrevoked_consent(db: AsyncSession, user_id: int, purpose: str) -> bool:
    """存在任意未撤回的同用途授权（不论版本）——用于区分「条款升版」与「未授权」。"""
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


async def effective_release_for(
    db: AsyncSession, instance: StewardServiceInstance
) -> StewardPilotRelease | None:
    """覆盖该实例包版本与当前环境的有效放行记录（S2；无则阻断激活）。"""
    now = now_utc()
    rows = (
        await db.execute(
            select(StewardPilotRelease)
            .where(
                StewardPilotRelease.package_id == instance.package_id,
                StewardPilotRelease.package_version_id == instance.package_version_id,
                StewardPilotRelease.env == settings.APP_ENV,
                StewardPilotRelease.revoked_at.is_(None),
                StewardPilotRelease.effective_from <= now,
                (StewardPilotRelease.effective_until.is_(None))
                | (StewardPilotRelease.effective_until >= now),
            )
            .order_by(StewardPilotRelease.id.desc())
        )
    ).scalars().all()
    return rows[0] if rows else None


async def release_missing_reason(db: AsyncSession, instance: StewardServiceInstance) -> str | None:
    release = await effective_release_for(db, instance)
    return None if release is not None else (
        f"试点放行未配置或不适用（需覆盖当前环境 {settings.APP_ENV} 与该服务包版本）"
    )


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
    """本人确认激活：九项门禁 + 试点放行全过才转 active 并生成日历；幂等返回原实例。

    与激活同一事务写激活凭证快照（A1）：门禁判定、条款版本、签发计划版本、责任人、
    起止时间。preparing 草稿不产生凭证——只有本人确认且门禁全过才固化事实并开始计时。
    """
    instance = await lazy_transition(db, instance)
    if instance.status == StewardInstanceStatus.TERMINATED.value:
        raise HTTPException(status_code=409, detail="该服务已终止，无法激活")
    if instance.status != StewardInstanceStatus.PREPARING.value:
        return instance  # 已激活 / 到期 / 结案：幂等返回原实例

    checks = await evaluate_prep_checks(db, instance)
    missing = prep_missing(checks)
    release_reason = await release_missing_reason(db, instance)
    if release_reason is not None:
        missing.append("pilot_release")
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"message": "激活条件未全部满足", "missing": missing},
        )

    # 激活前重取关键事实（凭证快照用；evaluate_prep_checks 之后到这里的窗口极小，
    # 但 plan/staff 以数据库当前值为准，不信任请求开始时的内存快照）
    plan = await valid_care_plan(db, instance.user_id, instance.physician_id)
    if plan is None:  # 理论上已被门禁拦下，防御性兜底
        raise HTTPException(status_code=409, detail={"message": "激活条件未全部满足", "missing": ["physician_plan"]})
    # 激活凭证只覆盖两项门禁授权（平台服务 + 设备数据）；电话类授权（切片 E）
    # 不是激活门禁，不在此复核
    consent_versions: dict[str, str] = {}
    for purpose, check_key in (
        (CONSENT_PURPOSE_STEWARD_SERVICE, "platform_consent"),
        (CONSENT_PURPOSE_STEWARD_DEVICE_DATA, "device_vendor_consent"),
    ):
        terms = await current_terms(db, purpose)
        matched = (
            await _matched_consent(db, instance.user_id, purpose, terms.version)
            if terms is not None
            else None
        )
        if terms is None or matched is None:
            raise HTTPException(
                status_code=409,
                detail={"message": "激活条件未全部满足", "missing": [check_key]},
            )
        consent_versions[purpose] = terms.version

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
            plan_version=plan.version,
        )
    )
    if result.rowcount != 1:
        # 并发重复激活：另一请求已成功，读回原实例（无未提交变更，不 rollback）
        await db.refresh(instance)
        if instance.status == StewardInstanceStatus.ACTIVE.value:
            return instance
        raise HTTPException(status_code=409, detail="激活状态冲突，请刷新后查看")

    db.add(
        StewardActivationCredential(
            instance_id=instance.id,
            user_id=instance.user_id,
            confirmed_at=activated,
            gate_snapshot={
                row.check_type: {"status": row.status, "reason": row.missing_reason}
                for row in checks
            },
            consent_versions=consent_versions,
            plan_version=plan.version,
            butler_id=instance.butler_id,
            backup_butler_id=instance.backup_butler_id,
            physician_id=instance.physician_id,
            service_started_at=activated,
            service_ends_at=ends_at,
        )
    )
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
            "plan_version": plan.version,
            "consent_versions": consent_versions,
        },
    )
    await audit(
        db,
        action="steward.instance.activate",
        resource_type="steward_instance",
        resource_id=instance.id,
        detail={"service_days": instance.service_days, "plan_version": plan.version},
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
    """当前服务日：active 按当日；paused 冻结在暂停开始日（计时已停止，A2）。"""
    if instance.activated_at is None:
        return None
    today = sh_today()
    if instance.status == StewardInstanceStatus.PAUSED.value:
        open_pause = (
            await db.execute(
                select(StewardPauseRecord.started_at)
                .where(
                    StewardPauseRecord.instance_id == instance.id,
                    StewardPauseRecord.ended_at.is_(None),
                )
                .order_by(StewardPauseRecord.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if open_pause is not None:
            pause_day = as_utc(open_pause).astimezone(SHANGHAI).date()
            today = min(today, pause_day)
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
    db: AsyncSession,
    user: User,
    instance: StewardServiceInstance,
    task_id: int,
    result: str | None,
    backfill_reason: str | None = None,
) -> StewardTask:
    """本人完成本人任务（A3 门禁）：仅 active 实例、不早于当日、历史补录须填原因。"""
    task = await db.get(StewardTask, task_id)
    if task is None or task.instance_id != instance.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.assignee_type != "user":
        raise HTTPException(status_code=403, detail="该任务由服务方完成，请勿自行操作")
    if task.status != StewardTaskStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="该任务已完成或已取消")
    if instance.status == StewardInstanceStatus.PAUSED.value:
        raise HTTPException(status_code=409, detail="服务暂停中，暂不能完成任务；恢复后继续")
    if instance.status != StewardInstanceStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail="服务不在服务中，不能完成任务")
    today = sh_today()
    if task.service_date > today:
        raise HTTPException(status_code=409, detail="不能提前完成未来任务")
    is_backfill = task.service_date < today
    if is_backfill and not (backfill_reason and backfill_reason.strip()):
        raise HTTPException(status_code=422, detail="补录历史任务须填写补录原因")
    task.status = StewardTaskStatus.DONE.value
    task.result = result
    task.completed_at = now_utc()
    if is_backfill:
        task.backfill_reason = backfill_reason
    await add_timeline(
        db,
        instance,
        "task_completed",
        actor_type="user",
        actor_id=user.id,
        actor_name=user_actor_name(user),
        detail={
            "task_id": task.id,
            "task_type": task.task_type,
            "service_day": task.service_day,
            "backfill": is_backfill,
        },
    )
    await db.commit()
    await db.refresh(task)
    return task


async def record_task_result(
    db: AsyncSession,
    admin,
    task_id: int,
    result: str,
    actual_occurred_at: datetime | None = None,
    backfill_reason: str | None = None,
) -> StewardTask:
    """后台登记任务结果（A3）：未来日期须补录字段；结果不覆盖（重复 409）。"""
    task = await db.get(StewardTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != StewardTaskStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="该任务已有结果，更正不覆盖原记录")
    today = sh_today()
    is_backfill = task.service_date > today
    if is_backfill and (actual_occurred_at is None or not (backfill_reason and backfill_reason.strip())):
        raise HTTPException(
            status_code=422, detail="登记未来日期任务须提供实际发生时间与补录原因"
        )
    task.status = StewardTaskStatus.DONE.value
    task.result = result
    task.completed_at = now_utc()
    task.completed_by_admin_id = admin.id
    if is_backfill:
        task.actual_occurred_at = actual_occurred_at
        task.backfill_reason = backfill_reason
    instance = await db.get(StewardServiceInstance, task.instance_id)
    if instance is not None:
        await add_timeline(
            db,
            instance,
            "task_completed",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.name,
            detail={
                "task_id": task.id,
                "task_type": task.task_type,
                "service_day": task.service_day,
                "backfill": is_backfill,
            },
        )
    await audit(
        db,
        action="steward.task.result",
        resource_type="steward_task",
        resource_id=task.id,
        detail={
            "service_day": task.service_day,
            "task_type": task.task_type,
            "backfill": is_backfill,
        },
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
    if instance.butler_id is not None and instance.butler_id == instance.backup_butler_id:
        raise HTTPException(status_code=422, detail="负责管家与替补不能为同一人")
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
    # 暂停即时停止该实例触达意图（恢复后按顺延日期重新生成；sweep 兜底）
    from app.services import outreach_service

    await outreach_service.stop_for_instance(db, instance.id, "service_paused")
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
    # 补时口径（契约固定）：医师建议暂停 → 全额；服务方核心中断严格大于 24h
    # （> 86400s，即 24h+1s 起）→ 全额；恰 24h 与不足 24h → 不补时仅记录并补交任务；
    # 用户申请 / 其他 → 不补时。
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
        # 平移范围（A2）：暂停开始日（上海日期）当天及之后的全部待办——暂停期间滑入
        # 过去日期的任务一并顺延补交；暂停开始日之前已到期的本人任务不平移（用户不
        # 记录不顺延，走补录通道）。
        pause_start_date = as_utc(record.started_at).astimezone(SHANGHAI).date()
        shiftable_tasks = (
            await db.execute(
                select(StewardTask).where(
                    StewardTask.instance_id == instance.id,
                    StewardTask.status == StewardTaskStatus.PENDING.value,
                    StewardTask.service_date >= pause_start_date,
                )
            )
        ).scalars().all()
        for task in shiftable_tasks:
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
                "shifted_tasks": len(shiftable_tasks),
                "pause_start_date": pause_start_date.isoformat(),
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


async def _open_high_priority_tickets(
    db: AsyncSession, instance_id: int
) -> int:
    """未结高优先级医师工单数（切片 D 起查真表；口径：high 且未结且未明确转交）。"""
    from app.services import professional_service

    return await professional_service.open_high_priority_count(db, instance_id)


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
    if await _open_high_priority_tickets(db, instance.id) > 0:
        raise HTTPException(status_code=409, detail="存在未结高优先级医师工单，须处理或明确转交后才能结案")
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
    # 切片 D：结案同事务生成版本化报告 v1（服务数据摘要 + 高优先级转交依据）
    from app.services import professional_service

    await professional_service.create_closure_report(db, instance, closure_report)
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


# ───────────────────────── 授权（S1：条款版本化） ─────────────────────────


async def publish_terms(
    db: AsyncSession, admin, purpose: str, version: str, title: str, text: str, note: str | None
) -> StewardConsentTerms:
    """发布新条款版本：同 purpose 旧 active 版本转 retired，新版本即刻成为授权依据。"""
    if purpose not in STEWARD_CONSENT_PURPOSES:
        raise HTTPException(status_code=422, detail="未知的授权用途")
    duplicate = (
        await db.execute(
            select(StewardConsentTerms).where(
                StewardConsentTerms.purpose == purpose,
                StewardConsentTerms.version == version,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该条款版本号已存在")
    await db.execute(
        update(StewardConsentTerms)
        .where(
            StewardConsentTerms.purpose == purpose,
            StewardConsentTerms.status == "active",
        )
        .values(status="retired")
    )
    terms = StewardConsentTerms(
        purpose=purpose,
        version=version,
        title=title,
        text=text,
        note=note,
        created_by_admin_id=admin.id,
    )
    db.add(terms)
    await audit(
        db,
        action="steward.terms.publish",
        resource_type="steward_consent_terms",
        detail={"purpose": purpose, "version": version},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(terms)
    return terms


async def list_terms(
    db: AsyncSession, purpose: str | None = None
) -> list[StewardConsentTerms]:
    stmt = select(StewardConsentTerms).order_by(StewardConsentTerms.id.desc())
    if purpose:
        stmt = stmt.where(StewardConsentTerms.purpose == purpose)
    return list((await db.execute(stmt)).scalars().all())


async def current_terms_all(db: AsyncSession) -> list[StewardConsentTerms]:
    """各 purpose 当前 active 条款（C 端展示用；无条款的 purpose 不出现）。"""
    rows = (
        await db.execute(
            select(StewardConsentTerms)
            .where(StewardConsentTerms.status == "active")
            .order_by(StewardConsentTerms.purpose.asc())
        )
    ).scalars().all()
    latest: dict[str, StewardConsentTerms] = {}
    for row in rows:  # 同 purpose 理论唯一 active，防御性取最新
        latest[row.purpose] = row
    return [latest[p] for p in sorted(latest)]


async def grant_steward_consent(
    db: AsyncSession, user: User, purpose: str, version: str
) -> MpUserConsent:
    """授予履约授权（S1）：先展示条款全文，本人确认时提交所见版本；服务端校验
    版本与当前条款一致并保存文本快照。未发布条款 503；版本不一致 409（旧版本
    不能冒充新版本同意）。同版本重复提交幂等返回原记录。"""
    if purpose not in STEWARD_CONSENT_PURPOSES:
        raise HTTPException(status_code=422, detail="未知的授权用途")
    terms = await current_terms(db, purpose)
    if terms is None:
        raise HTTPException(status_code=503, detail="该授权条款尚未配置，暂不能授权")
    if version != terms.version:
        raise HTTPException(status_code=409, detail="条款版本已更新，请查看最新条款")
    existing = await _matched_consent(db, user.id, purpose, terms.version)
    if existing is not None:
        return existing
    consent = MpUserConsent(user_id=user.id, purpose=purpose, version=terms.version, text=terms.text)
    db.add(consent)
    await audit(
        db,
        action="steward.consent.grant",
        resource_type="mp_user_consent",
        detail={"purpose": purpose, "version": terms.version},
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
                    MpUserConsent.version == terms.version,
                    MpUserConsent.revoked_at.is_(None),
                )
                .limit(1)
            )
        ).scalar_one()
        return existing
    await db.refresh(consent)
    return consent


async def consent_records(db: AsyncSession, user: User) -> list[dict]:
    """本人各 purpose 授权现状：已授权版本是否落后当前条款（outdated 须重新确认）。"""
    records: list[dict] = []
    for purpose in STEWARD_CONSENT_PURPOSES:
        terms = await current_terms(db, purpose)
        granted = (
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
        if granted is None:
            records.append(
                {"purpose": purpose, "granted": False, "version": None, "granted_at": None,
                 "consent_id": None, "current_version": terms.version if terms else None,
                 "outdated": False, "terms_available": terms is not None}
            )
        else:
            records.append(
                {"purpose": purpose, "granted": True, "version": granted.version,
                 "granted_at": granted.granted_at, "consent_id": granted.id,
                 "current_version": terms.version if terms else None,
                 "outdated": terms is not None and granted.version != terms.version,
                 "terms_available": terms is not None}
            )
    return records


async def on_consent_revoked(db: AsyncSession, consent: MpUserConsent) -> None:
    """撤回联动（S1，由公共撤回入口调用）：写本人非终态实例时间线 + 审计；
    auto 门禁即时回退由既有实时重判完成；切片 B/C/E 的执行器在副作用前统一重查授权。"""
    if not consent.purpose.startswith("steward_"):
        return
    user = await db.get(User, consent.user_id)
    if user is None:
        return
    instances = (
        await db.execute(
            select(StewardServiceInstance).where(
                StewardServiceInstance.user_id == consent.user_id,
                StewardServiceInstance.status.in_(_ACTIVE_LIKE_STATUSES),
            )
        )
    ).scalars().all()
    for instance in instances:
        await add_timeline(
            db,
            instance,
            "consent_revoked",
            actor_type="user",
            actor_id=consent.user_id,
            actor_name=user_actor_name(user),
            detail={"purpose": consent.purpose, "consent_id": consent.id},
        )
    await audit(
        db,
        action="steward.consent.revoke",
        resource_type="mp_user_consent",
        resource_id=consent.id,
        detail={"purpose": consent.purpose},
        actor_type="user",
        actor_id=consent.user_id,
        actor_name=user_actor_name(user),
    )
    if consent.purpose == CONSENT_PURPOSE_STEWARD_SERVICE:
        from app.services import outreach_service

        await outreach_service.stop_for_user(db, consent.user_id, "consent_revoked")
    if consent.purpose in (
        CONSENT_PURPOSE_STEWARD_PHONE_OUTREACH,
        CONSENT_PURPOSE_STEWARD_CALL_RECORDING,
    ):
        from app.services import phone_service

        await phone_service.stop_orders(
            db, [phone_service.PhoneCallOrder.user_id == consent.user_id], "consent_revoked"
        )
    await db.commit()


# ───────────────────────── 医师签发计划（A1） ─────────────────────────


async def create_care_plan(
    db: AsyncSession, admin, payload: dict
) -> StewardCarePlan:
    physician = await db.get(StewardStaff, payload["physician_id"])
    if physician is None or physician.role != "physician" or physician.status != "active":
        raise HTTPException(status_code=422, detail="签发人须为在职医师")
    user = await db.get(User, payload["user_id"])
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    duplicate = (
        await db.execute(
            select(StewardCarePlan).where(
                StewardCarePlan.user_id == payload["user_id"],
                StewardCarePlan.version == payload["version"],
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该用户已有同版本签发计划")
    rules_version = payload.get("rules_version")
    if rules_version:
        from app.services import professional_service

        await professional_service.validate_rules_version(db, payload["org_name"], rules_version)
    plan = StewardCarePlan(
        user_id=payload["user_id"],
        physician_id=payload["physician_id"],
        org_name=payload["org_name"],
        version=payload["version"],
        content=payload.get("content") or {},
        rules_version=rules_version,
        valid_until=payload.get("valid_until"),
        created_by_admin_id=admin.id,
    )
    db.add(plan)
    # 同用户存在 preparing 实例且签约医师一致 → 绑定并写时间线
    preparing = (
        await db.execute(
            select(StewardServiceInstance).where(
                StewardServiceInstance.user_id == payload["user_id"],
                StewardServiceInstance.status == StewardInstanceStatus.PREPARING.value,
            )
            .order_by(StewardServiceInstance.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if preparing is not None and preparing.physician_id == payload["physician_id"]:
        plan.instance_id = preparing.id
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该用户已有同版本签发计划")
    if plan.instance_id is not None:
        await add_timeline(
            db,
            preparing,
            "care_plan_bound",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.name,
            detail={"plan_id": plan.id, "version": plan.version},
        )
    await audit(
        db,
        action="steward.careplan.create",
        resource_type="steward_care_plan",
        resource_id=plan.id,
        detail={"user_id": plan.user_id, "version": plan.version, "physician_id": plan.physician_id},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(plan)
    return plan


async def revoke_care_plan(
    db: AsyncSession, admin, plan: StewardCarePlan, reason: str
) -> StewardCarePlan:
    if plan.revoked_at is not None:
        raise HTTPException(status_code=409, detail="该计划已撤销")
    plan.revoked_at = now_utc()
    plan.revoke_reason = reason
    await audit(
        db,
        action="steward.careplan.revoke",
        resource_type="steward_care_plan",
        resource_id=plan.id,
        detail={"reason": reason, "user_id": plan.user_id},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(plan)
    return plan


async def list_care_plans(db: AsyncSession, user_id: int | None) -> list[StewardCarePlan]:
    stmt = select(StewardCarePlan).order_by(StewardCarePlan.id.desc())
    if user_id:
        stmt = stmt.where(StewardCarePlan.user_id == user_id)
    return list((await db.execute(stmt)).scalars().all())


# ───────────────────────── 试点放行（S2） ─────────────────────────


def _validate_release_evidence(evidence: list[dict]) -> None:
    if not isinstance(evidence, list) or not evidence:
        raise HTTPException(status_code=422, detail="放行凭据不能为空")
    seen: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="凭据格式不正确")
        key = str(item.get("item", ""))
        if key not in PILOT_RELEASE_EVIDENCE_ITEMS:
            raise HTTPException(status_code=422, detail=f"未知凭据项：{key}")
        if key in seen:
            raise HTTPException(status_code=422, detail=f"凭据项重复：{key}")
        seen.add(key)
        source = str(item.get("source", "")).strip()
        reference = str(item.get("reference", "")).strip()
        if not source or not reference:
            raise HTTPException(
                status_code=422, detail=f"凭据 {key} 须给出真实来源与编号，不接受描述性勾选"
            )
    missing = [item for item in PILOT_RELEASE_EVIDENCE_ITEMS if item not in seen]
    if missing:
        raise HTTPException(status_code=422, detail=f"缺少放行凭据项：{', '.join(missing)}")


async def create_release(db: AsyncSession, admin, payload: dict) -> StewardPilotRelease:
    _validate_release_evidence(payload.get("evidence") or [])
    release = StewardPilotRelease(
        package_id=payload["package_id"],
        package_version_id=payload["package_version_id"],
        env=payload["env"],
        owner_name=payload["owner_name"],
        evidence=payload["evidence"],
        effective_until=payload.get("effective_until"),
        created_by_admin_id=admin.id,
    )
    db.add(release)
    await audit(
        db,
        action="steward.release.create",
        resource_type="steward_pilot_release",
        resource_id=release.id,
        detail={
            "package_version_id": release.package_version_id,
            "env": release.env,
            "owner": release.owner_name,
        },
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(release)
    return release


async def revoke_release(
    db: AsyncSession, admin, release: StewardPilotRelease, reason: str
) -> StewardPilotRelease:
    if release.revoked_at is not None:
        raise HTTPException(status_code=409, detail="该放行记录已撤销")
    release.revoked_at = now_utc()
    release.revoke_reason = reason
    await audit(
        db,
        action="steward.release.revoke",
        resource_type="steward_pilot_release",
        resource_id=release.id,
        detail={"reason": reason},
        actor_id=admin.id,
        actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(release)
    return release


async def list_releases(db: AsyncSession, package_id: int | None) -> list[StewardPilotRelease]:
    stmt = select(StewardPilotRelease).order_by(StewardPilotRelease.id.desc())
    if package_id:
        stmt = stmt.where(StewardPilotRelease.package_id == package_id)
    return list((await db.execute(stmt)).scalars().all())


# ───────────────────────── 生命周期扫描（A3） ─────────────────────────


async def lifecycle_sweep(db: AsyncSession) -> dict[str, int]:
    """周期推进（Celery beat / 手动）：到期 active → expired；权益作废 → terminated
    并取消待办。条件 UPDATE 幂等，多 worker / 重复执行只赢一次；与读路径共用同一组
    迁移函数与审计 action。无人访问页面也能及时停止后续任务。"""
    now = now_utc()
    expired = 0
    terminated = 0
    due = (
        await db.execute(
            select(StewardServiceInstance).where(
                StewardServiceInstance.status == StewardInstanceStatus.ACTIVE.value,
                StewardServiceInstance.ends_at.is_not(None),
                StewardServiceInstance.ends_at <= now,
            ).order_by(StewardServiceInstance.id.asc())
        )
    ).scalars().all()
    for instance in due:
        if await _expire_instance(db, instance):
            expired += 1
    void_rows = (
        await db.execute(
            select(StewardServiceInstance)
            .join(MpEntitlement, StewardServiceInstance.entitlement_id == MpEntitlement.id)
            .where(
                StewardServiceInstance.status.in_(_ACTIVE_LIKE_STATUSES),
                MpEntitlement.status == EntitlementStatus.VOID.value,
            )
            .order_by(StewardServiceInstance.id.asc())
        )
    ).scalars().all()
    for instance in void_rows:
        ent = await db.get(MpEntitlement, instance.entitlement_id)
        if await _terminate_instance(db, instance, (ent.void_reason if ent else None) or "权益已作废"):
            terminated += 1
    if due or void_rows:
        await db.commit()
    return {"expired": expired, "terminated": terminated}


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
    if (
        payload.get("status") == "inactive"
        and staff.status == "active"
        and staff.role == "butler"
    ):
        from app.services import outreach_service

        open_count = await outreach_service.open_intent_count_for_staff(db, staff.id)
        if open_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"该管家名下仍有 {open_count} 条未完成触达待办，须先完成触达交接再停用",
            )
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
