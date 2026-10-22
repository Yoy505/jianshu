"""触达任务域业务层（工作二切片 B，2026-10-22）：渠道无关的意图生成、停止、人工工作台与交接。

通道边界（最高优先级）：本层无任何发送调用与回执写入——`channel_receipt_ref` 恒为 null，
人工提交联系结果不得冒充通道成功。通道选定后另开接入切片。

并发与幂等：
- 生成以 UNIQUE(instance_id, purpose, service_date) 兜底，冲突读回原记录；
- 全部状态迁移为条件 UPDATE + rowcount 校验，多 worker / 重复提交只生效一次；
- 每次人工流转前重查实例状态、退订与授权（执行器副作用前重查原则）。
"""

from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    CONSENT_PURPOSE_STEWARD_SERVICE,
    OUTREACH_OPEN_STATUSES,
    MpUserConsent,
    OutreachAttempt,
    OutreachIntent,
    OutreachIntentStatus,
    OutreachPurpose,
    OutreachSubscription,
    StewardInstanceStatus,
    StewardServiceInstance,
    StewardStaff,
    StewardTask,
    StewardTaskStatus,
    StewardTaskType,
)
from app.schemas.common import as_utc
from app.services import steward_service
from app.services.audit_service import audit

UTC = timezone.utc

# 内容版本：daily 固定；follow_up 取任务计划版本（无则回退）
DAILY_CONTENT_VERSION = "daily_v1"
FOLLOWUP_FALLBACK_VERSION = "followup_v1"

STATUS_LABEL = {
    OutreachIntentStatus.PENDING_CHANNEL.value: "待通道配置",
    OutreachIntentStatus.PENDING_MANUAL.value: "待人工处理",
    OutreachIntentStatus.PENDING_CONFIRM.value: "待确认",
    OutreachIntentStatus.RESOLVED.value: "事项已办理",
    OutreachIntentStatus.FAILED.value: "失败",
    OutreachIntentStatus.CANCELLED.value: "已取消",
}

CHANNEL_STATUS_NOTE = "提醒通道待配置，服务期事项由管家人工跟进；不承诺每日送达"


def _now() -> datetime:
    return datetime.now(UTC)


async def _add_attempt(
    db: AsyncSession,
    intent: OutreachIntent,
    action: str,
    *,
    actor_type: str = "system",
    actor_admin_id: int | None = None,
    actor_name: str = "system",
    contact_method: str | None = None,
    outcome: str | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        OutreachAttempt(
            intent_id=intent.id,
            action=action,
            actor_type=actor_type,
            actor_admin_id=actor_admin_id,
            actor_name=actor_name,
            contact_method=contact_method,
            outcome=outcome,
            detail=detail,
        )
    )


def max_attempts() -> int:
    return max(1, int(getattr(settings, "STEWARD_OUTREACH_MAX_ATTEMPTS", 3) or 3))


# ───────────────────────── 生成（幂等） ─────────────────────────


async def _contactable(db: AsyncSession, user_id: int) -> bool:
    """可联系判定：未退订且平台服务授权未撤回（生成与复活前检查）。"""
    sub = await db.get(OutreachSubscription, user_id)
    if sub is not None and not bool(sub.subscribed):
        return False
    consented = (
        await db.execute(
            select(MpUserConsent.id).where(
                MpUserConsent.user_id == user_id,
                MpUserConsent.purpose == CONSENT_PURPOSE_STEWARD_SERVICE,
                MpUserConsent.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    return consented is not None


async def generate_for_instance_date(
    db: AsyncSession, instance: StewardServiceInstance, day: date
) -> int:
    """为某实例某服务日生成意图（仅 active 且可联系；幂等，返回新建/复活条数）。

    同一 (instance, purpose, service_date) 只有一条意图：已被暂停 / 退订 / 授权撤回
    停止的意图，在条件恢复（实例恢复服务、重新订阅、重新授权）时复活为待通道配置
    并留 generated 尝试；人工取消（manual_cancel）不自动复活。
    """
    if instance.status != StewardInstanceStatus.ACTIVE.value:
        return 0
    if not await _contactable(db, instance.user_id):
        return 0
    tasks = (
        await db.execute(
            select(StewardTask).where(
                StewardTask.instance_id == instance.id,
                StewardTask.service_date == day,
                StewardTask.task_type.in_(
                    [StewardTaskType.DAILY.value, StewardTaskType.FOLLOW_UP.value]
                ),
            )
        )).scalars().all()
    created = 0
    for task in tasks:
        purpose = (
            OutreachPurpose.DAILY_REMINDER.value
            if task.task_type == StewardTaskType.DAILY.value
            else OutreachPurpose.FOLLOW_UP.value
        )
        content_version = (
            DAILY_CONTENT_VERSION
            if task.task_type == StewardTaskType.DAILY.value
            else (task.plan_version or FOLLOWUP_FALLBACK_VERSION)
        )
        existing = (
            await db.execute(
                select(OutreachIntent).where(
                    OutreachIntent.instance_id == instance.id,
                    OutreachIntent.purpose == purpose,
                    OutreachIntent.service_date == day,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.status == OutreachIntentStatus.CANCELLED.value and existing.stop_reason not in (
                "manual_cancel",
                "instance_expired",
                "instance_terminated",
            ):
                # 条件恢复：复活同一意图（历史经尝试记录保留），并重挂当日任务
                result = await db.execute(
                    update(OutreachIntent)
                    .where(
                        OutreachIntent.id == existing.id,
                        OutreachIntent.status == OutreachIntentStatus.CANCELLED.value,
                    )
                    .values(
                        status=OutreachIntentStatus.PENDING_CHANNEL.value,
                        stop_reason=None,
                        contactable=True,
                        contact_note=None,
                        task_id=task.id,
                        content_version=content_version,
                        assignee_staff_id=None,
                    )
                )
                if result.rowcount == 1:
                    await _add_attempt(
                        db, existing, "generated",
                        detail={"resumed": True, "content_version": content_version},
                    )
                    created += 1
            continue
        intent = OutreachIntent(
            instance_id=instance.id,
            user_id=instance.user_id,
            task_id=task.id,
            service_date=day,
            purpose=purpose,
            content_version=content_version,
        )
        db.add(intent)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()  # 并发已建：回滚本条，继续后续（读回不重建）
            continue
        await _add_attempt(db, intent, "generated", detail={"content_version": content_version})
        created += 1
    if created:
        await db.commit()
    return created


async def outreach_sweep(db: AsyncSession, *, today: date | None = None) -> dict[str, int]:
    """周期推进（beat 每 5 分钟 / 手动）：生成当日意图 + 停止失效意图。幂等。"""
    day = today or steward_service.sh_today()
    generated = 0
    active_instances = (
        await db.execute(
            select(StewardServiceInstance).where(
                StewardServiceInstance.status == StewardInstanceStatus.ACTIVE.value
            )
        )).scalars().all()
    for instance in active_instances:
        generated += await generate_for_instance_date(db, instance, day)
    stopped = await stop_invalid_intents(db)
    return {"generated": generated, **stopped}


# ───────────────────────── 停止条件 ─────────────────────────


async def _cancel_intents(
    db: AsyncSession,
    where_clause,
    reason: str,
    *,
    action: str = "stopped",
    actor_type: str = "system",
    actor_admin_id: int | None = None,
    actor_name: str = "system",
) -> int:
    """按条件取消未终态意图（条件 UPDATE + 尝试记录留痕）。"""
    intents = (
        await db.execute(
            select(OutreachIntent).where(
                OutreachIntent.status.in_(OUTREACH_OPEN_STATUSES), *where_clause
            )
        )).scalars().all()
    moved = 0
    for intent in intents:
        result = await db.execute(
            update(OutreachIntent)
            .where(
                OutreachIntent.id == intent.id,
                OutreachIntent.status.in_(OUTREACH_OPEN_STATUSES),
            )
            .values(
                status=OutreachIntentStatus.CANCELLED.value,
                stop_reason=reason,
                contactable=False,
                contact_note=STATUS_NOTE_REASON.get(reason, reason),
            )
        )
        if result.rowcount == 1:
            await _add_attempt(
                db,
                intent,
                action,
                actor_type=actor_type,
                actor_admin_id=actor_admin_id,
                actor_name=actor_name,
                detail={"reason": reason},
            )
            moved += 1
    if moved:
        await db.commit()
    return moved


STATUS_NOTE_REASON = {
    "instance_expired": "服务已到期，停止后续触达",
    "instance_terminated": "服务已终止，停止后续触达",
    "service_paused": "服务暂停中，恢复后按顺延日期重新安排",
    "unsubscribed": "本人已退订每日提醒",
    "consent_revoked": "平台服务授权已撤回，停止后续触达",
    "manual_cancel": "人工取消",
}


async def stop_invalid_intents(db: AsyncSession) -> dict[str, int]:
    """扫描全部未终态意图，按实例状态 / 退订 / 授权撤回停止。"""
    paused = expired = terminated = unsubscribed = consent_revoked = 0
    # 1) 实例级：expired / terminated / paused
    rows = (
        await db.execute(
            select(OutreachIntent, StewardServiceInstance)
            .join(StewardServiceInstance, OutreachIntent.instance_id == StewardServiceInstance.id)
            .where(OutreachIntent.status.in_(OUTREACH_OPEN_STATUSES))
        )
    ).all()
    expired_ids, terminated_ids, paused_ids = [], [], []
    for intent, instance in rows:
        if instance.status == StewardInstanceStatus.TERMINATED.value:
            terminated_ids.append(intent.id)
        elif instance.status == StewardInstanceStatus.EXPIRED.value or (
            instance.status == StewardInstanceStatus.ACTIVE.value
            and instance.ends_at is not None
            and as_utc(instance.ends_at) <= _now()
        ):
            expired_ids.append(intent.id)
        elif instance.status == StewardInstanceStatus.PAUSED.value:
            paused_ids.append(intent.id)
    if expired_ids:
        expired = await _cancel_intents(
            db, [OutreachIntent.id.in_(expired_ids)], "instance_expired"
        )
    if terminated_ids:
        terminated = await _cancel_intents(
            db, [OutreachIntent.id.in_(terminated_ids)], "instance_terminated"
        )
    if paused_ids:
        paused = await _cancel_intents(db, [OutreachIntent.id.in_(paused_ids)], "service_paused")
    # 2) 退订：未终态且本人已退订
    unsub_rows = (
        await db.execute(
            select(OutreachIntent.id)
            .join(OutreachSubscription, OutreachIntent.user_id == OutreachSubscription.user_id)
            .where(
                OutreachIntent.status.in_(OUTREACH_OPEN_STATUSES),
                OutreachSubscription.subscribed == 0,  # type: ignore[comparison-overlap]
            )
        )
    ).scalars().all()
    if unsub_rows:
        unsubscribed = await _cancel_intents(
            db, [OutreachIntent.id.in_(unsub_rows)], "unsubscribed"
        )
    # 3) 平台服务授权撤回：该用途无未撤回授权
    revoked_users = set(
        (
            await db.execute(
                select(OutreachIntent.user_id)
                .where(OutreachIntent.status.in_(OUTREACH_OPEN_STATUSES))
                .group_by(OutreachIntent.user_id)
            )
        ).scalars().all()
    )
    for user_id in revoked_users:
        consented = (
            await db.execute(
                select(MpUserConsent.id).where(
                    MpUserConsent.user_id == user_id,
                    MpUserConsent.purpose == CONSENT_PURPOSE_STEWARD_SERVICE,
                    MpUserConsent.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if consented is None:
            consent_revoked += await _cancel_intents(
                db,
                [OutreachIntent.user_id == user_id],
                "consent_revoked",
            )
    return {
        "stopped_expired": expired,
        "stopped_terminated": terminated,
        "stopped_paused": paused,
        "stopped_unsubscribed": unsubscribed,
        "stopped_consent_revoked": consent_revoked,
    }


async def stop_for_user(db: AsyncSession, user_id: int, reason: str) -> int:
    """停止某人全部未终态意图（退订 / 撤回联动入口）。"""
    return await _cancel_intents(db, [OutreachIntent.user_id == user_id], reason)


async def stop_for_instance(db: AsyncSession, instance_id: int, reason: str) -> int:
    """停止某实例全部未终态意图（暂停 / 终止即时联动；sweep 兜底）。"""
    return await _cancel_intents(db, [OutreachIntent.instance_id == instance_id], reason)


# ───────────────────────── 人工工作台流转 ─────────────────────────


async def _get_intent(db: AsyncSession, intent_id: int) -> OutreachIntent:
    intent = await db.get(OutreachIntent, intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="触达意图不存在")
    return intent


async def _ensure_workable(db: AsyncSession, intent: OutreachIntent) -> StewardServiceInstance:
    """流转前重查（执行器副作用前重查原则）：实例、退订、授权任一失效即 409。"""
    instance = await db.get(StewardServiceInstance, intent.instance_id)
    if instance is None or instance.status != StewardInstanceStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail="服务不在服务中，触达已停止")
    sub = await db.get(OutreachSubscription, intent.user_id)
    if sub is not None and not bool(sub.subscribed):
        raise HTTPException(status_code=409, detail="本人已退订，不能继续触达")
    consented = (
        await db.execute(
            select(MpUserConsent.id).where(
                MpUserConsent.user_id == intent.user_id,
                MpUserConsent.purpose == CONSENT_PURPOSE_STEWARD_SERVICE,
                MpUserConsent.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if consented is None:
        raise HTTPException(status_code=409, detail="平台服务授权已撤回，不能继续触达")
    return instance


async def _transition(
    db: AsyncSession,
    intent: OutreachIntent,
    from_statuses: list[str],
    values: dict,
) -> None:
    result = await db.execute(
        update(OutreachIntent)
        .where(OutreachIntent.id == intent.id, OutreachIntent.status.in_(from_statuses))
        .values(**values)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="触达状态冲突，请刷新后重试")


async def _check_staff(db: AsyncSession, staff_id: int) -> StewardStaff:
    staff = await db.get(StewardStaff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    if staff.role != "butler":
        raise HTTPException(status_code=422, detail="触达责任人须为管家")
    if staff.status != "active":
        raise HTTPException(status_code=409, detail=f"人员 {staff.name} 已停用，不能指派")
    return staff


async def route_manual(
    db: AsyncSession, admin, intent_id: int, staff_id: int, note: str | None
) -> OutreachIntent:
    """转人工：pending_channel → pending_manual 并指派管家。"""
    intent = await _get_intent(db, intent_id)
    await _ensure_workable(db, intent)
    staff = await _check_staff(db, staff_id)
    await _transition(
        db,
        intent,
        [OutreachIntentStatus.PENDING_CHANNEL.value],
        {
            "status": OutreachIntentStatus.PENDING_MANUAL.value,
            "assignee_staff_id": staff_id,
        },
    )
    await _add_attempt(
        db, intent, "routed_manual",
        actor_type="admin", actor_admin_id=admin.id, actor_name=admin.name,
        outcome=note, detail={"staff_id": staff_id},
    )
    await audit(
        db, action="outreach.intent.route", resource_type="outreach_intent",
        resource_id=intent.id, detail={"staff_id": staff_id, "note": note},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def assign(
    db: AsyncSession, admin, intent_id: int, staff_id: int, note: str | None
) -> OutreachIntent:
    """领取 / 改派：pending_manual 内变更责任人（改派留 reassigned 记录）。"""
    intent = await _get_intent(db, intent_id)
    await _ensure_workable(db, intent)
    staff = await _check_staff(db, staff_id)
    old = intent.assignee_staff_id
    await _transition(
        db,
        intent,
        [OutreachIntentStatus.PENDING_MANUAL.value],
        {"assignee_staff_id": staff_id},
    )
    await _add_attempt(
        db, intent, "reassigned" if old is not None else "assigned",
        actor_type="admin", actor_admin_id=admin.id, actor_name=admin.name,
        outcome=note, detail={"from_staff_id": old, "to_staff_id": staff_id},
    )
    await audit(
        db, action="outreach.intent.assign", resource_type="outreach_intent",
        resource_id=intent.id, detail={"from_staff_id": old, "to_staff_id": staff_id},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def release(db: AsyncSession, admin, intent_id: int, reason: str) -> OutreachIntent:
    """退回未指派池：仍 pending_manual，assignee 置空。"""
    intent = await _get_intent(db, intent_id)
    await _ensure_workable(db, intent)
    old = intent.assignee_staff_id
    await _transition(
        db,
        intent,
        [OutreachIntentStatus.PENDING_MANUAL.value],
        {"assignee_staff_id": None},
    )
    await _add_attempt(
        db, intent, "released",
        actor_type="admin", actor_admin_id=admin.id, actor_name=admin.name,
        outcome=reason, detail={"from_staff_id": old},
    )
    await audit(
        db, action="outreach.intent.release", resource_type="outreach_intent",
        resource_id=intent.id, detail={"reason": reason},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def submit_result(
    db: AsyncSession, admin, intent_id: int, contact_method: str, outcome: str, note: str | None
) -> OutreachIntent:
    """人工完成联系：pending_manual（已指派）→ pending_confirm。记录方式 / 处理人 / 结果；
    人工联系不冒充通道成功——回执引用恒空。"""
    intent = await _get_intent(db, intent_id)
    await _ensure_workable(db, intent)
    if intent.assignee_staff_id is None:
        raise HTTPException(status_code=409, detail="意图尚未指派责任人，先领取或指派")
    await _transition(
        db,
        intent,
        [OutreachIntentStatus.PENDING_MANUAL.value],
        {"status": OutreachIntentStatus.PENDING_CONFIRM.value, "result": outcome},
    )
    await _add_attempt(
        db, intent, "submitted",
        actor_type="admin", actor_admin_id=admin.id, actor_name=admin.name,
        contact_method=contact_method, outcome=outcome, detail={"note": note},
    )
    await audit(
        db, action="outreach.intent.submit", resource_type="outreach_intent",
        resource_id=intent.id,
        detail={"contact_method": contact_method},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def confirm(db: AsyncSession, admin, intent_id: int, note: str | None) -> OutreachIntent:
    """确认办理：pending_confirm → resolved（事项已办理）。"""
    intent = await _get_intent(db, intent_id)
    await _transition(
        db,
        intent,
        [OutreachIntentStatus.PENDING_CONFIRM.value],
        {"status": OutreachIntentStatus.RESOLVED.value},
    )
    await _add_attempt(
        db, intent, "confirmed",
        actor_type="admin", actor_admin_id=admin.id, actor_name=admin.name, outcome=note,
    )
    await audit(
        db, action="outreach.intent.confirm", resource_type="outreach_intent",
        resource_id=intent.id, detail={"note": note},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def reject(db: AsyncSession, admin, intent_id: int, reason: str) -> OutreachIntent:
    """驳回重办：pending_confirm → pending_manual（attempts+1）；达上限 → failed。"""
    intent = await _get_intent(db, intent_id)
    new_attempts = (intent.attempts or 0) + 1
    final = (
        OutreachIntentStatus.FAILED.value
        if new_attempts >= max_attempts()
        else OutreachIntentStatus.PENDING_MANUAL.value
    )
    await _transition(
        db,
        intent,
        [OutreachIntentStatus.PENDING_CONFIRM.value],
        {"status": final, "attempts": new_attempts},
    )
    await _add_attempt(
        db, intent, "rejected",
        actor_type="admin", actor_admin_id=admin.id, actor_name=admin.name,
        outcome=reason, detail={"attempts": new_attempts, "final": final},
    )
    await audit(
        db, action="outreach.intent.reject", resource_type="outreach_intent",
        resource_id=intent.id, detail={"reason": reason, "attempts": new_attempts, "final": final},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(intent)
    return intent


async def cancel_intent(db: AsyncSession, admin, intent_id: int, reason: str) -> OutreachIntent:
    """人工取消（manual_cancel）。"""
    intent = await _get_intent(db, intent_id)
    moved = await _cancel_intents(
        db,
        [OutreachIntent.id == intent.id],
        "manual_cancel",
        action="cancelled",
        actor_type="admin",
        actor_admin_id=admin.id,
        actor_name=admin.name,
    )
    if moved == 0:
        raise HTTPException(status_code=409, detail="该意图已是终态，不能取消")
    await audit(
        db, action="outreach.intent.cancel", resource_type="outreach_intent",
        resource_id=intent.id, detail={"reason": reason},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.refresh(intent)
    return intent


# ───────────────────────── 离岗交接 ─────────────────────────


async def handover(
    db: AsyncSession, admin, from_staff_id: int, to_staff_id: int, reason: str
) -> int:
    """批量交接：转交某管家全部未终态意图并留痕；to 须在职管家。from 允许已停用（善后交接）。"""
    if from_staff_id == to_staff_id:
        raise HTTPException(status_code=422, detail="交接双方不能为同一人")
    from_staff = await db.get(StewardStaff, from_staff_id)
    if from_staff is None:
        raise HTTPException(status_code=404, detail="原责任人不存在")
    to_staff = await _check_staff(db, to_staff_id)
    intents = (
        await db.execute(
            select(OutreachIntent).where(
                OutreachIntent.assignee_staff_id == from_staff_id,
                OutreachIntent.status.in_(OUTREACH_OPEN_STATUSES),
            )
        )).scalars().all()
    moved = 0
    for intent in intents:
        result = await db.execute(
            update(OutreachIntent)
            .where(
                OutreachIntent.id == intent.id,
                OutreachIntent.assignee_staff_id == from_staff_id,
                OutreachIntent.status.in_(OUTREACH_OPEN_STATUSES),
            )
            .values(assignee_staff_id=to_staff_id)
        )
        if result.rowcount == 1:
            await _add_attempt(
                db, intent, "reassigned",
                actor_type="admin", actor_admin_id=admin.id, actor_name=admin.name,
                outcome=reason,
                detail={"from_staff_id": from_staff_id, "to_staff_id": to_staff_id},
            )
            moved += 1
    await audit(
        db, action="outreach.handover", resource_type="outreach_intent",
        detail={
            "from_staff_id": from_staff_id,
            "to_staff_id": to_staff_id,
            "moved": moved,
            "reason": reason,
        },
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    return moved


async def open_intent_count_for_staff(db: AsyncSession, staff_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(OutreachIntent)
                .where(
                    OutreachIntent.assignee_staff_id == staff_id,
                    OutreachIntent.status.in_(OUTREACH_OPEN_STATUSES),
                )
            )
        ).scalar()
        or 0
    )


# ───────────────────────── 退订（C 端） ─────────────────────────


async def subscription_status(db: AsyncSession, user_id: int) -> dict:
    sub = await db.get(OutreachSubscription, user_id)
    return {
        "subscribed": bool(sub.subscribed) if sub is not None else True,
        "channel_decided": False,
        "status_note": CHANNEL_STATUS_NOTE,
    }


async def set_subscription(db: AsyncSession, user, subscribed: bool) -> dict:
    """退订 / 重新订阅。退订立即取消本人全部未终态意图；重新订阅后按后续服务日重新生成。
    退订不影响服务履约本身，也不等于申请退款。"""
    sub = await db.get(OutreachSubscription, user.id)
    if sub is None:
        sub = OutreachSubscription(user_id=user.id, subscribed=subscribed)
        db.add(sub)
    sub.subscribed = subscribed
    stopped = 0
    if not subscribed:
        stopped = await stop_for_user(db, user.id, "unsubscribed")
    await audit(
        db,
        action="outreach.subscription.set",
        resource_type="outreach_subscription",
        resource_id=user.id,
        detail={"subscribed": subscribed, "stopped": stopped},
        actor_type="user",
        actor_id=user.id,
        actor_name=steward_service.user_actor_name(user),
    )
    await db.commit()
    return {**await subscription_status(db, user.id), "stopped": stopped}
