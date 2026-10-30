"""专业任务与异常工单域业务层（工作二切片 D，2026-10-22）。

医疗边界（最高优先级）：无签发资料时只做格式及数据质量检查，不运行医学判断；
本层不做自动诊断 / 治疗建议 / 风险分级，不决定电话对象。签发身份一致：工单结果与
报告签发须由绑定指派医师的后台账号录入（防冒充）。
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EMERGENCY_NOTE,
    MEDICAL_ORDER_TYPES,
    OPEN_ORDER_STATUSES,
    ProfessionalOrderStatus,
    ProfessionalOrderType,
    ProfessionalPriority,
    StewardCarePlan,
    StewardCareRules,
    StewardFollowupRecord,
    StewardInstanceStatus,
    StewardProfessionalEvent,
    StewardProfessionalOrder,
    StewardServiceInstance,
    StewardServiceReport,
    StewardStaff,
    StewardStaffAdminLink,
    StewardTask,
    StewardTaskStatus,
    StewardTaskType,
)
from app.schemas.common import as_utc
from app.services import steward_service
from app.services.audit_service import audit

UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


# ───────────────────────── 机构规则版本 ─────────────────────────


async def publish_care_rules(
    db: AsyncSession, admin, org_name: str, version: str, content: dict, note: str | None
) -> StewardCareRules:
    duplicate = (
        await db.execute(
            select(StewardCareRules).where(
                StewardCareRules.org_name == org_name, StewardCareRules.version == version
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该机构已有同版本规则")
    await db.execute(
        update(StewardCareRules)
        .where(StewardCareRules.org_name == org_name, StewardCareRules.status == "active")
        .values(status="retired", retired_at=now_utc())
    )
    rules = StewardCareRules(
        org_name=org_name, version=version, content=content, note=note, created_by_admin_id=admin.id
    )
    db.add(rules)
    await audit(
        db, action="steward.carerules.publish", resource_type="steward_care_rules", resource_id=rules.id,
        detail={"org_name": org_name, "version": version}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(rules)
    return rules


async def list_care_rules(db: AsyncSession) -> list[StewardCareRules]:
    return list((await db.execute(select(StewardCareRules).order_by(StewardCareRules.id.desc()))).scalars().all())


async def validate_rules_version(db: AsyncSession, org_name: str, version: str) -> None:
    exists = (
        await db.execute(
            select(StewardCareRules.id).where(
                StewardCareRules.org_name == org_name,
                StewardCareRules.version == version,
                StewardCareRules.status == "active",
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=422, detail=f"机构 {org_name} 不存在有效的规则版本 {version}")


# ───────────────────────── 账号↔人员绑定 ─────────────────────────


async def create_staff_link(db: AsyncSession, admin, staff_id: int, admin_id: int) -> StewardStaffAdminLink:
    staff = await db.get(StewardStaff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    if staff.status != "active":
        raise HTTPException(status_code=422, detail="人员已停用，不能新建账号绑定")
    target = await link_admin_or_404(db, admin_id)
    if target is None:
        raise HTTPException(status_code=404, detail="后台账号不存在")
    link = StewardStaffAdminLink(staff_id=staff_id, admin_id=admin_id, created_by_admin_id=admin.id)
    db.add(link)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该绑定已存在")
    await audit(
        db, action="steward.stafflink.create", resource_type="steward_staff_admin_link", resource_id=link.id,
        detail={"staff_id": staff_id, "admin_id": admin_id}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(link)
    return link


async def link_admin_or_404(db: AsyncSession, admin_id: int):
    from app.models import AdminUser

    return await db.get(AdminUser, admin_id)


async def delete_staff_link(db: AsyncSession, admin, link_id: int) -> None:
    link = await db.get(StewardStaffAdminLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="绑定不存在")
    await db.delete(link)
    await audit(
        db, action="steward.stafflink.delete", resource_type="steward_staff_admin_link", resource_id=link_id,
        detail={"staff_id": link.staff_id, "admin_id": link.admin_id},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()


async def admin_bound_to_physician(db: AsyncSession, admin_id: int, physician_id: int) -> bool:
    exists = (
        await db.execute(
            select(StewardStaffAdminLink.id).where(
                StewardStaffAdminLink.admin_id == admin_id,
                StewardStaffAdminLink.staff_id == physician_id,
            )
        )
    ).scalar_one_or_none()
    return exists is not None


async def require_physician_identity(db: AsyncSession, admin, physician_id: int | None, *, action: str) -> StewardStaff:
    """可信签发身份链（F04）：工单完成 / 更正报告等专业操作共用。

    依次核验：存在指派医师 → 人员角色为医师 → 人员在职且资质有效 → 当前账号绑定该医师。任一环节
    不成立即 403，不区分"运营账号""停用医师""已转交"以外的内部细节。调用方仍须在条件
    更新里带上同一约束（见 complete_order），避免核验与写入之间人员被停用或工单被转交。
    """
    if physician_id is None:
        raise HTTPException(status_code=403, detail=f"该对象没有指派医师，不能{action}")
    physician = await db.get(StewardStaff, physician_id)
    if physician is None or physician.role != "physician":
        raise HTTPException(status_code=403, detail=f"指派对象不是医师，不能{action}")
    if physician.status != "active":
        raise HTTPException(status_code=403, detail=f"该医师已停用，不能继续{action}；请先转交")
    if not physician.credential_valid(now_utc()):
        raise HTTPException(status_code=403, detail=f"该医师资质未核验、已撤销或已到期，不能{action}")
    if not await admin_bound_to_physician(db, admin.id, physician.id):
        raise HTTPException(
            status_code=403, detail=f"仅绑定当前指派医师的账号可{action}，运营账号不能代医师签发"
        )
    return physician


def _active_physician_ids():
    return select(StewardStaff.id).where(
        StewardStaff.role == "physician", StewardStaff.status == "active",
        StewardStaff.credential_verified_at.is_not(None),
        StewardStaff.credential_revoked_at.is_(None),
        or_(StewardStaff.credential_valid_until.is_(None), StewardStaff.credential_valid_until > now_utc()),
    )


async def guard_physician_authorization(db: AsyncSession, physician_id: int, admin_id: int | None = None) -> None:
    """写入前再次核验并锁住资质行与绑定行，撤销与签发按事务顺序生效。"""
    if admin_id is not None:
        link = (await db.execute(select(StewardStaffAdminLink.id).where(
            StewardStaffAdminLink.staff_id == physician_id, StewardStaffAdminLink.admin_id == admin_id,
        ).with_for_update())).scalar_one_or_none()
        if link is None:
            raise HTTPException(status_code=409, detail="医师账号绑定已变化，请刷新后重试")
    result = await db.execute(update(StewardStaff).where(
        # 资格条件必须直接约束被更新的行；IN 子查询可能保留锁等待前的快照。
        StewardStaff.id == physician_id,
        StewardStaff.role == "physician", StewardStaff.status == "active",
        StewardStaff.credential_verified_at.is_not(None),
        StewardStaff.credential_revoked_at.is_(None),
        or_(StewardStaff.credential_valid_until.is_(None), StewardStaff.credential_valid_until > now_utc()),
    ).values(status=StewardStaff.status))
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="医师状态或资质已变化，请刷新后重试")
    physician = await db.get(StewardStaff, physician_id, populate_existing=True)
    # 有效期也可能在等待行锁期间到达；签发证据须使用锁后的最新核验记录。
    if not physician.credential_valid(now_utc()):
        raise HTTPException(status_code=409, detail="医师资质已到期，请刷新后重试")


def validate_physician_signer(physician: StewardStaff) -> None:
    """人员行已锁定；实例或工单行等待结束后仍须检查时间推进导致的到期。"""
    if physician.role != "physician" or physician.status != "active" or not physician.credential_valid(now_utc()):
        raise HTTPException(status_code=409, detail="签发时医师状态或资质已失效，请刷新后重试")


def physician_signature(physician: StewardStaff, admin) -> dict:
    """仅服务端生成签发时的责任证据，历史绑定变化不抹除当时的签发事实。"""
    validate_physician_signer(physician)
    return {"physician_id": physician.id, "admin_id": admin.id,
            "credential_verified_at": as_utc(physician.credential_verified_at).isoformat()}


# ───────────────────────── 专业工单 ─────────────────────────


async def _add_event(
    db: AsyncSession, order: StewardProfessionalOrder, action: str, admin=None, detail: dict | None = None
) -> None:
    db.add(
        StewardProfessionalEvent(
            order_id=order.id,
            action=action,
            actor_admin_id=admin.id if admin is not None else None,
            actor_name=admin.name if admin is not None else "system",
            detail=detail,
        )
    )


async def create_order(db: AsyncSession, admin, payload: dict) -> StewardProfessionalOrder:
    instance = await db.get(StewardServiceInstance, payload["instance_id"])
    if instance is None:
        raise HTTPException(status_code=404, detail="服务实例不存在")
    physician = await db.get(StewardStaff, payload["physician_id"])
    if physician is None or physician.role != "physician" or physician.status != "active":
        raise HTTPException(status_code=422, detail="指派对象须为在职医师")
    order_type = payload["order_type"]
    care_plan: StewardCarePlan | None = None
    rules_version: str | None = None
    if order_type in MEDICAL_ORDER_TYPES:
        # 无签发阻断：医学判断类工单必须存在该用户 + 该医师的有效签发计划
        care_plan = await steward_service.valid_care_plan(db, instance.user_id, physician.id)
        if care_plan is None:
            raise HTTPException(
                status_code=422,
                detail="无有效签发计划，不能创建医学判断类工单（只允许格式与数据质量检查）",
            )
        rules_version = care_plan.rules_version
    order = StewardProfessionalOrder(
        instance_id=instance.id,
        user_id=instance.user_id,
        order_type=order_type,
        title=payload["title"],
        detail=payload.get("detail"),
        priority=payload.get("priority", ProfessionalPriority.NORMAL.value),
        physician_id=physician.id,
        task_id=payload.get("task_id"),
        care_plan_version=care_plan.version if care_plan is not None else None,
        rules_version=rules_version,
        due_at=payload.get("due_at"),
        created_by_admin_id=admin.id,
    )
    db.add(order)
    await db.flush()
    await _add_event(
        db, order, "created", admin,
        detail={"order_type": order_type, "priority": order.priority,
                "care_plan_version": order.care_plan_version, "rules_version": rules_version},
    )
    await audit(
        db, action="steward.proforder.create", resource_type="steward_professional_order",
        resource_id=order.id,
        detail={"order_type": order_type, "priority": order.priority, "instance_id": instance.id},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def start_order(db: AsyncSession, admin, order: StewardProfessionalOrder) -> StewardProfessionalOrder:
    result = await db.execute(
        update(StewardProfessionalOrder)
        .where(
            StewardProfessionalOrder.id == order.id,
            StewardProfessionalOrder.status == ProfessionalOrderStatus.PENDING.value,
        )
        .values(status=ProfessionalOrderStatus.IN_PROGRESS.value)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="仅待处理工单可开始")
    await _add_event(db, order, "started", admin)
    await db.commit()
    await db.refresh(order)
    return order


async def transfer_order(
    db: AsyncSession, admin, order: StewardProfessionalOrder, to_physician_id: int, reason: str
) -> StewardProfessionalOrder:
    if order.status not in OPEN_ORDER_STATUSES:
        raise HTTPException(status_code=409, detail="仅未结工单可转交")
    receiver = await db.get(StewardStaff, to_physician_id)
    if receiver is None or receiver.role != "physician" or receiver.status != "active":
        raise HTTPException(status_code=422, detail="接收人须为在职医师")
    old = order.physician_id
    result = await db.execute(
        update(StewardProfessionalOrder)
        .where(
            StewardProfessionalOrder.id == order.id,
            StewardProfessionalOrder.status.in_(OPEN_ORDER_STATUSES),
        )
        .values(physician_id=to_physician_id, transferred_at=now_utc(), transfer_reason=reason)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="转交状态冲突，请刷新后重试")
    await _add_event(
        db, order, "transferred", admin,
        detail={"from_physician_id": old, "to_physician_id": to_physician_id, "reason": reason},
    )
    await audit(
        db, action="steward.proforder.transfer", resource_type="steward_professional_order",
        resource_id=order.id,
        detail={"from_physician_id": old, "to_physician_id": to_physician_id, "reason": reason},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def complete_order(
    db: AsyncSession, admin, order: StewardProfessionalOrder, result_text: str, result_meta: dict | None
) -> StewardProfessionalOrder:
    if order.status not in OPEN_ORDER_STATUSES:
        raise HTTPException(status_code=409, detail="仅未结工单可完成")
    # 签发身份一致（F04 可信身份链）：录入账号须绑定当前指派医师，且该医师在职（转交后按接收医师校验）
    physician = await require_physician_identity(db, admin, order.physician_id, action="录入工单结果")
    await guard_physician_authorization(db, physician.id, admin.id)
    result = await db.execute(
        update(StewardProfessionalOrder)
        .where(
            StewardProfessionalOrder.id == order.id,
            StewardProfessionalOrder.status.in_(OPEN_ORDER_STATUSES),
            # 条件更新同时锁定"仍指派给该医师"与"该医师仍在职"，覆盖停用 / 转交与完成并发
            StewardProfessionalOrder.physician_id == physician.id,
            StewardProfessionalOrder.physician_id.in_(_active_physician_ids()),
        )
        .values(
            status=ProfessionalOrderStatus.DONE.value,
            result=result_text,
            result_meta={**(result_meta or {}), "physician_signature": physician_signature(physician, admin)},
            completed_at=now_utc(),
            completed_by_admin_id=admin.id,
        )
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="完成状态冲突（工单已变更、已转交或医师已停用），请刷新后重试")
    await _add_event(db, order, "completed", admin, detail={
        "result": result_text[:200], "physician_signature": physician_signature(physician, admin),
    })
    await audit(
        db, action="steward.proforder.complete", resource_type="steward_professional_order",
        resource_id=order.id,
        detail={"order_type": order.order_type, "physician_id": order.physician_id},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def cancel_order(db: AsyncSession, admin, order: StewardProfessionalOrder, reason: str) -> StewardProfessionalOrder:
    if order.status not in OPEN_ORDER_STATUSES:
        raise HTTPException(status_code=409, detail="仅未结工单可取消")
    result = await db.execute(
        update(StewardProfessionalOrder)
        .where(
            StewardProfessionalOrder.id == order.id,
            StewardProfessionalOrder.status.in_(OPEN_ORDER_STATUSES),
        )
        .values(status=ProfessionalOrderStatus.CANCELLED.value)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="取消状态冲突，请刷新后重试")
    await _add_event(db, order, "cancelled", admin, detail={"reason": reason})
    await audit(
        db, action="steward.proforder.cancel", resource_type="steward_professional_order",
        resource_id=order.id, detail={"reason": reason}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def open_order_count_for_physician(db: AsyncSession, physician_id: int) -> int:
    """停用前置校验（F04）：该医师名下未结专业工单数。"""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(StewardProfessionalOrder)
                .where(
                    StewardProfessionalOrder.physician_id == physician_id,
                    StewardProfessionalOrder.status.in_(OPEN_ORDER_STATUSES),
                )
            )
        ).scalar()
        or 0
    )


async def open_high_priority_count(db: AsyncSession, instance_id: int) -> int:
    """结案阻断口径：high 且未结且未明确转交。"""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(StewardProfessionalOrder)
                .where(
                    StewardProfessionalOrder.instance_id == instance_id,
                    StewardProfessionalOrder.priority == ProfessionalPriority.HIGH.value,
                    StewardProfessionalOrder.status.in_(OPEN_ORDER_STATUSES),
                    StewardProfessionalOrder.transferred_at.is_(None),
                )
            )
        ).scalar()
        or 0
    )


async def professional_sweep(db: AsyncSession) -> dict[str, int]:
    """超时升级（beat 每 15 分钟）：未结且已过截止且未升级 → 记升级事实 + 事件 + 审计。
    升级为待人工处置标记，不自动外呼（电话属切片 E 且须授权）。"""
    now = now_utc()
    overdue = (
        (
            await db.execute(
                select(StewardProfessionalOrder).where(
                    StewardProfessionalOrder.status.in_(OPEN_ORDER_STATUSES),
                    StewardProfessionalOrder.due_at.is_not(None),
                    StewardProfessionalOrder.due_at < now,
                    StewardProfessionalOrder.escalated_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    escalated = 0
    for order in overdue:
        result = await db.execute(
            update(StewardProfessionalOrder)
            .where(
                StewardProfessionalOrder.id == order.id,
                StewardProfessionalOrder.escalated_at.is_(None),
            )
            .values(escalated_at=now_utc(), escalation_note="已超过处理期限，升级待人工处置")
        )
        if result.rowcount == 1:
            await _add_event(db, order, "escalated", detail={"due_at": as_utc(order.due_at).isoformat()})
            await audit(
                db, action="steward.proforder.escalate", resource_type="steward_professional_order",
                resource_id=order.id, detail={"due_at": as_utc(order.due_at).isoformat()},
                actor_type="system",
            )
            escalated += 1
    if escalated:
        await db.commit()
    return {"escalated": escalated}


# ───────────────────────── 版本化报告 ─────────────────────────


async def create_closure_report(
    db: AsyncSession, admin, instance: StewardServiceInstance, closure_report: str
) -> StewardServiceReport:
    """结案报告 v1（close_instance 同事务调用）：服务数据摘要 + 签发医师。"""
    physician = await require_physician_identity(db, admin, instance.physician_id, action="签发结案报告")
    await guard_physician_authorization(db, physician.id, admin.id)
    task_counts: dict[str, int] = {}
    rows = (
        await db.execute(
            select(StewardTask.task_type, StewardTask.status, func.count())
            .where(StewardTask.instance_id == instance.id)
            .group_by(StewardTask.task_type, StewardTask.status)
        )
    ).all()
    for task_type, status, count in rows:
        task_counts.setdefault(task_type, {})[status] = int(count)
    transferred = int(
        (
            await db.execute(
                select(func.count())
                .select_from(StewardProfessionalOrder)
                .where(
                    StewardProfessionalOrder.instance_id == instance.id,
                    StewardProfessionalOrder.transferred_at.is_not(None),
                )
            )
        ).scalar()
        or 0
    )
    validate_physician_signer(physician)
    report = StewardServiceReport(
        instance_id=instance.id,
        user_id=instance.user_id,
        version=1,
        report_type="closure",
        title="45 天服务结案报告",
        content=closure_report,
        data_summary={"tasks": task_counts, "compensated_seconds": instance.compensated_seconds or 0,
                      "high_priority_transfers": transferred},
        basis="结案报告依据期末复评与高优先级工单处理/转交记录" if transferred else "结案报告依据期末复评记录",
        physician_id=instance.physician_id,
        created_by_admin_id=admin.id,
    )
    db.add(report)
    return report


async def create_correction_report(
    db: AsyncSession, admin, instance: StewardServiceInstance, title: str, content: str, basis: str | None
) -> StewardServiceReport:
    """结案后更正：形成新版本（supersedes 链），不覆盖原报告；须绑定实例签约医师。"""
    if instance.status != StewardInstanceStatus.CLOSED.value:
        raise HTTPException(status_code=409, detail="仅已结案实例可更正报告")
    physician = await require_physician_identity(db, admin, instance.physician_id, action="签发更正报告")
    await guard_physician_authorization(db, physician.id, admin.id)
    locked = await db.execute(update(StewardServiceInstance).where(
        StewardServiceInstance.id == instance.id,
        StewardServiceInstance.status == StewardInstanceStatus.CLOSED.value,
        StewardServiceInstance.physician_id == physician.id,
    ).values(physician_id=StewardServiceInstance.physician_id))
    if locked.rowcount != 1:
        raise HTTPException(status_code=409, detail="实例状态或指派医师已变化，请刷新后重试")
    latest = (
        await db.execute(
            select(StewardServiceReport)
            .where(StewardServiceReport.instance_id == instance.id)
            .order_by(StewardServiceReport.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        raise HTTPException(status_code=409, detail="尚无原报告，不能更正")
    validate_physician_signer(physician)
    report = StewardServiceReport(
        instance_id=instance.id,
        user_id=instance.user_id,
        version=latest.version + 1,
        report_type="correction",
        title=title,
        content=content,
        basis=basis,
        physician_id=instance.physician_id,
        supersedes_id=latest.id,
        created_by_admin_id=admin.id,
    )
    db.add(report)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="报告版本冲突，请刷新后重试")
    await audit(
        db, action="steward.report.correct", resource_type="steward_service_report", resource_id=report.id,
        detail={"instance_id": instance.id, "version": report.version, "supersedes": latest.id},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(report)
    return report


# ───────────────────────── 结构化随访 ─────────────────────────


async def record_followup(
    db: AsyncSession, admin, instance: StewardServiceInstance, task_id: int, payload: dict
) -> StewardFollowupRecord:
    task = await db.get(StewardTask, task_id)
    if task is None or task.instance_id != instance.id or task.task_type != StewardTaskType.FOLLOW_UP.value:
        raise HTTPException(status_code=422, detail="仅本人实例的随访任务可录制结构化记录")
    duplicate = (
        await db.execute(
            select(StewardFollowupRecord).where(StewardFollowupRecord.task_id == task_id)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该随访已有结构化记录（更正走报告版本化）")
    record = StewardFollowupRecord(
        instance_id=instance.id,
        user_id=instance.user_id,
        task_id=task_id,
        service_day=task.service_day,
        summary=payload["summary"],
        data_quality_note=payload.get("data_quality_note"),
        next_action=payload.get("next_action"),
        recorded_by_admin_id=admin.id,
    )
    db.add(record)
    # 随访 steward 任务同步标记完成（有结构化记录即有真实结果）
    await db.execute(
        update(StewardTask)
        .where(
            StewardTask.id == task_id,
            StewardTask.status == StewardTaskStatus.PENDING.value,
        )
        .values(status=StewardTaskStatus.DONE.value, completed_at=now_utc(), result=payload["summary"][:500])
    )
    await audit(
        db, action="steward.followup.record", resource_type="steward_followup_record", resource_id=record.id,
        detail={"instance_id": instance.id, "service_day": task.service_day},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(record)
    return record


async def instance_professional_view(db: AsyncSession, instance: StewardServiceInstance) -> dict:
    """C 端专业进展聚合：工单（不含内部备注）、报告、随访与固定急救文案。"""
    orders = (
        (
            await db.execute(
                select(StewardProfessionalOrder)
                .where(StewardProfessionalOrder.instance_id == instance.id)
                .order_by(StewardProfessionalOrder.id.desc())
            )
        )
        .scalars()
        .all()
    )
    reports = (
        (
            await db.execute(
                select(StewardServiceReport)
                .where(StewardServiceReport.instance_id == instance.id)
                .order_by(StewardServiceReport.version.asc())
            )
        )
        .scalars()
        .all()
    )
    followups = (
        (
            await db.execute(
                select(StewardFollowupRecord)
                .where(StewardFollowupRecord.instance_id == instance.id)
                .order_by(StewardFollowupRecord.service_day.asc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "orders": [
            {
                "id": o.id, "order_type": o.order_type, "title": o.title, "status": o.status,
                "priority": o.priority, "due_at": o.due_at,
                "escalated": o.escalated_at is not None,
                "transferred": o.transferred_at is not None,
                "completed_at": o.completed_at,
            }
            for o in orders
        ],
        "reports": [
            {
                "id": r.id, "version": r.version, "report_type": r.report_type, "title": r.title,
                "content": r.content, "issued_at": r.issued_at,
            }
            for r in reports
        ],
        "followups": [
            {
                "id": f.id, "service_day": f.service_day, "summary": f.summary,
                "data_quality_note": f.data_quality_note, "next_action": f.next_action,
            }
            for f in followups
        ],
        "emergency_note": EMERGENCY_NOTE,
    }
