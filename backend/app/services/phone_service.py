"""CCC 电话通路域业务层（工作二切片 E，2026-10-22）：隔离状态机 + 适配接口。

外部边界：真实外呼保持关闭——生产适配器注册表为空（审核通过 → pending_config 待配置）；
mock / 合成适配器结果标记 simulated，不计真实接通。回调验签密钥未配置即拒收。

AI 边界：本层不做任何医学解释；话术内容是唯一告知来源（须审核 active）；
错号结果不携带任何服务或健康信息。
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    CONSENT_PURPOSE_STEWARD_CALL_RECORDING,
    CONSENT_PURPOSE_STEWARD_PHONE_OUTREACH,
    PHONE_OPEN_STATUSES,
    MpUserConsent,
    PhoneCallAttempt,
    PhoneCallOrder,
    PhoneContact,
    PhoneContactConsent,
    PhoneOrderStatus,
    PhoneScript,
    StewardInstanceStatus,
    StewardServiceInstance,
    User,
)
from app.schemas.common import as_utc, mask_phone
from app.services import steward_service
from app.services.audit_service import audit

UTC = timezone.utc

NO_CCC_NOTE = "CCC 通路待配置（号码 / 准入 / 合同 / 话术审核未落实，真实外呼关闭）"


def now_utc() -> datetime:
    return datetime.now(UTC)


def max_attempts() -> int:
    return max(1, int(getattr(settings, "STEWARD_PHONE_MAX_ATTEMPTS", 3) or 3))


def retry_delay() -> timedelta:
    minutes = max(1, int(getattr(settings, "STEWARD_PHONE_RETRY_DELAY_MINUTES", 30) or 30))
    return timedelta(minutes=minutes)


# ───────────────────────── CCC 适配接口 ─────────────────────────


class CCCAdapter(Protocol):
    """CCC 适配接口：真实供应商确定后实现并注册（凭据 / 签名 / 回执另验）。"""

    async def place_call(self, order: PhoneCallOrder, script: PhoneScript, phone: str) -> dict:
        """返回 {outcome, detail?}；outcome 见尝试枚举。真实实现由回调收尾，
        place_call 仅发起（发起回执引用由回调验签后落库）。"""


_ADAPTERS: dict[str, CCCAdapter] = {}


def register_adapter(key: str, adapter: CCCAdapter) -> None:
    """注册 CCC 适配器（仅测试 / 开发注入合成适配器；生产恒空）。"""
    _ADAPTERS[key] = adapter


def clear_adapters() -> None:
    _ADAPTERS.clear()


def adapter_ready() -> bool:
    return bool(_ADAPTERS)


def _adapter() -> CCCAdapter | None:
    return next(iter(_ADAPTERS.values()), None)


# ───────────────────────── 话术 ─────────────────────────


async def publish_script(
    db: AsyncSession, admin, payload: dict
) -> PhoneScript:
    duplicate = (
        await db.execute(
            select(PhoneScript).where(
                PhoneScript.code == payload["code"], PhoneScript.version == payload["version"]
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该话术版本已存在")
    script = PhoneScript(
        code=payload["code"],
        version=payload["version"],
        title=payload["title"],
        content=payload["content"],
        target_type=payload["target_type"],
        requires_recording=payload.get("requires_recording", False),
        created_by_admin_id=admin.id,
    )
    db.add(script)
    await audit(
        db, action="phone.script.publish", resource_type="phone_script", resource_id=script.id,
        detail={"code": payload["code"], "version": payload["version"]},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(script)
    return script


async def review_script(db: AsyncSession, admin, script: PhoneScript, approve: bool, note: str) -> PhoneScript:
    if script.status != "draft":
        raise HTTPException(status_code=409, detail="仅草稿话术可审核")
    if approve:
        await db.execute(
            update(PhoneScript)
            .where(PhoneScript.code == script.code, PhoneScript.status == "active")
            .values(status="retired")
        )
        script.status = "active"
    else:
        script.status = "rejected"
    script.reviewed_by_admin_id = admin.id
    script.review_note = note
    await audit(
        db, action="phone.script.review", resource_type="phone_script", resource_id=script.id,
        detail={"approve": approve, "note": note}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(script)
    return script


# ───────────────────────── 联系人 ─────────────────────────


async def create_contact(db: AsyncSession, admin, payload: dict) -> PhoneContact:
    user = await db.get(User, payload["user_id"])
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    contact = PhoneContact(
        user_id=payload["user_id"],
        name=payload["name"],
        relation=payload["relation"],
        phone=payload["phone"],
        identity_note=payload["identity_note"],
        created_by_admin_id=admin.id,
    )
    db.add(contact)
    await audit(
        db, action="phone.contact.create", resource_type="phone_contact", resource_id=contact.id,
        detail={"user_id": payload["user_id"], "relation": payload["relation"]},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(contact)
    return contact


async def set_contact_consent(
    db: AsyncSession, admin, contact: PhoneContact, status: str, source: str, note: str | None
) -> PhoneContact:
    if status not in ("granted", "refused", "revoked"):
        raise HTTPException(status_code=422, detail="状态须为 granted / refused / revoked")
    contact.consent_status = status
    contact.consent_source = source
    contact.consent_at = now_utc()
    stopped = 0
    if status in ("refused", "revoked"):
        stopped = await stop_orders(
            db, [PhoneCallOrder.contact_id == contact.id], "contact_refused"
        )
    await audit(
        db, action="phone.contact.consent", resource_type="phone_contact", resource_id=contact.id,
        detail={"status": status, "source": source, "stopped": stopped},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(contact)
    return contact


# ───────────────────────── 授权判定（三类分立） ─────────────────────────


async def _purpose_consent_ok(db: AsyncSession, user_id: int, purpose: str) -> bool:
    from app.services.steward_service import _matched_consent, current_terms

    terms = await current_terms(db, purpose)
    if terms is None:
        return False
    return await _matched_consent(db, user_id, purpose, terms.version) is not None


async def _dispatch_precheck(db: AsyncSession, order: PhoneCallOrder) -> str | None:
    """调度前重查（副作用前重查原则）：返回阻断原因或 None。"""
    instance = await db.get(StewardServiceInstance, order.instance_id)
    if instance is None or instance.status not in (
        StewardInstanceStatus.ACTIVE.value, StewardInstanceStatus.PAUSED.value,
    ):
        return "instance_closed"
    if not await _purpose_consent_ok(db, order.user_id, CONSENT_PURPOSE_STEWARD_PHONE_OUTREACH):
        return "consent_revoked"
    if order.requires_recording and not await _purpose_consent_ok(
        db, order.user_id, CONSENT_PURPOSE_STEWARD_CALL_RECORDING
    ):
        return "consent_revoked"
    if order.target_type == "contact":
        contact = await db.get(PhoneContact, order.contact_id) if order.contact_id else None
        if contact is None or contact.consent_status != PhoneContactConsent.GRANTED.value:
            return "contact_refused"
    script = await db.get(PhoneScript, order.script_id)
    if script is None or script.status != "active":
        return "script_retired"
    return None


# ───────────────────────── 工单 ─────────────────────────


async def create_order(db: AsyncSession, admin, payload: dict) -> PhoneCallOrder:
    instance = await db.get(StewardServiceInstance, payload["instance_id"])
    if instance is None:
        raise HTTPException(status_code=404, detail="服务实例不存在")
    script = await db.get(PhoneScript, payload["script_id"])
    if script is None or script.status != "active":
        raise HTTPException(status_code=422, detail="话术须为审核通过版本")
    contact = None
    if payload["target_type"] == "contact":
        contact = await db.get(PhoneContact, payload.get("contact_id") or 0)
        if contact is None or contact.user_id != instance.user_id:
            raise HTTPException(status_code=422, detail="联系人不存在或不属于该用户")
        if contact.consent_status != PhoneContactConsent.GRANTED.value:
            raise HTTPException(status_code=422, detail="联系人未授权外呼（本人同意不等于联系人同意）")
    else:
        # 本人外呼须有本人电话授权（版本匹配条款）
        if not await _purpose_consent_ok(db, instance.user_id, CONSENT_PURPOSE_STEWARD_PHONE_OUTREACH):
            raise HTTPException(status_code=422, detail="本人电话外呼授权缺失或条款已更新")
    order = PhoneCallOrder(
        instance_id=instance.id,
        user_id=instance.user_id,
        purpose=payload["purpose"],
        target_type=payload["target_type"],
        contact_id=contact.id if contact is not None else None,
        script_id=script.id,
        script_code=script.code,
        script_version=script.version,
        requires_recording=script.requires_recording,
        created_by_admin_id=admin.id,
    )
    db.add(order)
    await audit(
        db, action="phone.order.create", resource_type="phone_call_order", resource_id=order.id,
        detail={"target_type": order.target_type, "script": f"{script.code}:{script.version}",
                "requires_recording": order.requires_recording},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def review_order(db: AsyncSession, admin, order: PhoneCallOrder, approve: bool, note: str) -> PhoneCallOrder:
    if order.status != PhoneOrderStatus.PENDING_REVIEW.value:
        raise HTTPException(status_code=409, detail="仅待审核工单可审核")
    if not approve:
        return await _cancel(db, admin, order, "manual_cancel", note)
    new_status = (
        PhoneOrderStatus.QUEUED.value if adapter_ready() else PhoneOrderStatus.PENDING_CONFIG.value
    )
    result = await db.execute(
        update(PhoneCallOrder)
        .where(
            PhoneCallOrder.id == order.id,
            PhoneCallOrder.status == PhoneOrderStatus.PENDING_REVIEW.value,
        )
        .values(status=new_status)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="审核状态冲突，请刷新后重试")
    await audit(
        db, action="phone.order.review", resource_type="phone_call_order", resource_id=order.id,
        detail={"approve": approve, "status": new_status, "note": note},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def takeover_order(db: AsyncSession, admin, order: PhoneCallOrder, note: str) -> PhoneCallOrder:
    if order.status not in PHONE_OPEN_STATUSES:
        raise HTTPException(status_code=409, detail="工单已终态，不能接管")
    result = await db.execute(
        update(PhoneCallOrder)
        .where(PhoneCallOrder.id == order.id, PhoneCallOrder.status.in_(PHONE_OPEN_STATUSES))
        .values(
            status=PhoneOrderStatus.TRANSFERRED_HUMAN.value,
            taken_over_by_admin_id=admin.id,
            taken_over_at=now_utc(),
        )
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="接管状态冲突，请刷新后重试")
    db.add(
        PhoneCallAttempt(
            order_id=order.id, outcome="needs_human", actor="admin", actor_admin_id=admin.id,
            detail={"note": note}, started_at=now_utc(), finished_at=now_utc(),
        )
    )
    await audit(
        db, action="phone.order.takeover", resource_type="phone_call_order", resource_id=order.id,
        detail={"note": note}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def complete_order_human(db: AsyncSession, admin, order: PhoneCallOrder, result_text: str) -> PhoneCallOrder:
    if order.status != PhoneOrderStatus.TRANSFERRED_HUMAN.value:
        raise HTTPException(status_code=409, detail="须先人工接管再录入结果")
    if order.taken_over_by_admin_id != admin.id:
        raise HTTPException(status_code=403, detail="仅接管人可录入结果")
    result = await db.execute(
        update(PhoneCallOrder)
        .where(
            PhoneCallOrder.id == order.id,
            PhoneCallOrder.status == PhoneOrderStatus.TRANSFERRED_HUMAN.value,
        )
        .values(status=PhoneOrderStatus.COMPLETED.value, result=result_text)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="完成状态冲突，请刷新后重试")
    db.add(
        PhoneCallAttempt(
            order_id=order.id, outcome="completed", actor="admin", actor_admin_id=admin.id,
            detail={"result": result_text[:200]}, started_at=now_utc(), finished_at=now_utc(),
        )
    )
    await audit(
        db, action="phone.order.complete", resource_type="phone_call_order", resource_id=order.id,
        detail={}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(order)
    return order


async def cancel_order(db: AsyncSession, admin, order: PhoneCallOrder, reason: str) -> PhoneCallOrder:
    return await _cancel(db, admin, order, "manual_cancel", reason)


async def _cancel(db: AsyncSession, admin, order: PhoneCallOrder, stop_reason: str, note: str) -> PhoneCallOrder:
    if order.status not in PHONE_OPEN_STATUSES:
        raise HTTPException(status_code=409, detail="工单已终态")
    result = await db.execute(
        update(PhoneCallOrder)
        .where(PhoneCallOrder.id == order.id, PhoneCallOrder.status.in_(PHONE_OPEN_STATUSES))
        .values(status=PhoneOrderStatus.CANCELLED.value, stop_reason=stop_reason)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="停止状态冲突，请刷新后重试")
    await audit(
        db, action="phone.order.cancel", resource_type="phone_call_order", resource_id=order.id,
        detail={"stop_reason": stop_reason, "note": note},
        actor_id=admin.id if admin is not None else None,
        actor_name=admin.name if admin is not None else "system",
    )
    await db.commit()
    await db.refresh(order)
    return order


async def stop_orders(db: AsyncSession, where_clause, reason: str) -> int:
    """按条件停止未终态工单（撤回 / 拒绝联动）。"""
    orders = (
        (await db.execute(select(PhoneCallOrder).where(PhoneCallOrder.status.in_(PHONE_OPEN_STATUSES), *where_clause)))
        .scalars()
        .all()
    )
    moved = 0
    for order in orders:
        result = await db.execute(
            update(PhoneCallOrder)
            .where(PhoneCallOrder.id == order.id, PhoneCallOrder.status.in_(PHONE_OPEN_STATUSES))
            .values(status=PhoneOrderStatus.CANCELLED.value, stop_reason=reason)
        )
        if result.rowcount == 1:
            db.add(
                PhoneCallAttempt(
                    order_id=order.id, outcome="blocked", detail={"reason": reason},
                    started_at=now_utc(), finished_at=now_utc(),
                )
            )
            moved += 1
    if moved:
        await db.commit()
    return moved


# ───────────────────────── 调度（有限重试） ─────────────────────────


async def dispatch_order(db: AsyncSession, order_id: int) -> dict:
    """单飞调度：条件 UPDATE queued→in_progress；前置重查；适配器拨打；结果落尝试。
    无适配器保持 / 置 pending_config。"""
    order = await db.get(PhoneCallOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="工单不存在")

    # pending_config 且适配器已就绪 → 回到 queued
    if order.status == PhoneOrderStatus.PENDING_CONFIG.value:
        if adapter_ready():
            await db.execute(
                update(PhoneCallOrder)
                .where(
                    PhoneCallOrder.id == order.id,
                    PhoneCallOrder.status == PhoneOrderStatus.PENDING_CONFIG.value,
                )
                .values(status=PhoneOrderStatus.QUEUED.value)
            )
            await db.commit()
            await db.refresh(order)
        else:
            return {"status": order.status, "detail": NO_CCC_NOTE}
    if order.status != PhoneOrderStatus.QUEUED.value:
        return {"status": order.status, "detail": "非待拨打状态"}
    if order.next_retry_at is not None and as_utc(order.next_retry_at) > now_utc():
        return {"status": order.status, "detail": "未到重试时间"}
    if (order.attempts or 0) >= max_attempts():
        await db.execute(
            update(PhoneCallOrder)
            .where(PhoneCallOrder.id == order.id)
            .values(status=PhoneOrderStatus.FAILED.value)
        )
        await db.commit()
        return {"status": "failed", "detail": "重试耗尽"}

    blocked_reason = await _dispatch_precheck(db, order)
    if blocked_reason is not None:
        return await _stop_dispatch_blocked(db, order, blocked_reason)

    adapter = _adapter()
    if adapter is None:
        await db.execute(
            update(PhoneCallOrder)
            .where(
                PhoneCallOrder.id == order.id,
                PhoneCallOrder.status == PhoneOrderStatus.QUEUED.value,
            )
            .values(status=PhoneOrderStatus.PENDING_CONFIG.value)
        )
        await db.commit()
        return {"status": "pending_config", "detail": NO_CCC_NOTE}

    # 单飞：仅一个 worker 能把 queued 置 in_progress
    result = await db.execute(
        update(PhoneCallOrder)
        .where(
            PhoneCallOrder.id == order.id,
            PhoneCallOrder.status == PhoneOrderStatus.QUEUED.value,
        )
        .values(status=PhoneOrderStatus.IN_PROGRESS.value)
    )
    if result.rowcount != 1:
        return {"status": "in_progress", "detail": "另一执行中（单飞保护）"}

    started = now_utc()
    script = await db.get(PhoneScript, order.script_id)
    phone = await _target_phone(db, order)
    try:
        outcome_data = await adapter.place_call(order, script, phone)
        outcome = outcome_data.get("outcome", "no_answer")
        simulated = bool(outcome_data.get("simulated", True))  # 合成适配器默认标记模拟
    except Exception as exc:  # noqa: BLE001 —— 适配器异常按失败尝试处理
        outcome, simulated = "no_answer", True
        outcome_data = {"error": f"{type(exc).__name__}: {exc}"}

    new_attempts = (order.attempts or 0) + 1
    values: dict[str, Any] = {"attempts": new_attempts}
    if outcome in ("no_answer", "busy"):
        values["status"] = PhoneOrderStatus.QUEUED.value  # 有限重试
        values["next_retry_at"] = now_utc() + retry_delay()
        if new_attempts >= max_attempts():
            values["status"] = PhoneOrderStatus.FAILED.value
            values["next_retry_at"] = None
    elif outcome == "wrong_number":
        # 错号：终态；不披露任何服务或健康信息（detail 仅记错号结果）
        values["status"] = PhoneOrderStatus.WRONG_NUMBER.value
        outcome_data = {"outcome": "wrong_number"}
    elif outcome == "refused":
        values["status"] = PhoneOrderStatus.REFUSED.value  # 拒绝停止
    elif outcome == "needs_human":
        values["status"] = PhoneOrderStatus.TRANSFERRED_HUMAN.value
    elif outcome == "completed":
        values["status"] = PhoneOrderStatus.COMPLETED.value
    else:
        values["status"] = PhoneOrderStatus.FAILED.value
    await db.execute(
        update(PhoneCallOrder).where(PhoneCallOrder.id == order.id).values(**values)
    )
    db.add(
        PhoneCallAttempt(
            order_id=order.id, outcome=outcome, actor="system", simulated=simulated,
            detail=outcome_data, started_at=started, finished_at=now_utc(),
        )
    )
    await audit(
        db, action="phone.order.dispatch", resource_type="phone_call_order", resource_id=order.id,
        detail={"outcome": outcome, "attempts": new_attempts, "simulated": simulated},
        actor_type="system",
    )
    await db.commit()
    return {"status": values["status"], "outcome": outcome, "attempts": new_attempts}


async def _stop_dispatch_blocked(db: AsyncSession, order: PhoneCallOrder, reason: str) -> dict:
    await db.execute(
        update(PhoneCallOrder)
        .where(PhoneCallOrder.id == order.id, PhoneCallOrder.status.in_(PHONE_OPEN_STATUSES))
        .values(status=PhoneOrderStatus.CANCELLED.value, stop_reason=reason)
    )
    db.add(
        PhoneCallAttempt(
            order_id=order.id, outcome="blocked", detail={"reason": reason},
            started_at=now_utc(), finished_at=now_utc(),
        )
    )
    await db.commit()
    return {"status": "cancelled", "detail": reason}


async def _target_phone(db: AsyncSession, order: PhoneCallOrder) -> str:
    if order.target_type == "contact" and order.contact_id:
        contact = await db.get(PhoneContact, order.contact_id)
        return contact.phone if contact else ""
    user = await db.get(User, order.user_id)
    return (user.phone or "") if user else ""


async def phone_call_sweep(db: AsyncSession) -> dict:
    """beat 每 5 分钟：批量调度到期 queued / pending_config 工单（幂等，单飞保护）。"""
    orders = (
        (
            await db.execute(
                select(PhoneCallOrder.id).where(
                    PhoneCallOrder.status.in_(
                        [PhoneOrderStatus.QUEUED.value, PhoneOrderStatus.PENDING_CONFIG.value]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for order_id in orders:
        try:
            result = await dispatch_order(db, order_id)
        except HTTPException:
            result = {"status": "error"}
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {"total": len(orders), **counts}


# ───────────────────────── 回调（验签 / 幂等 / 查原状态） ─────────────────────────


def verify_signature(body: bytes, signature: str) -> bool:
    secret = getattr(settings, "STEWARD_PHONE_CCC_SECRET", "") or ""
    if not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_callback(db: AsyncSession, body: bytes, signature: str | None) -> dict:
    """CCC 回调：验签（密钥未配置 / 不符 → 403）；幂等（重复回调返回现状）；
    未知 / 终态单先查原状态，不盲目应用。"""
    if not signature or not verify_signature(body, signature):
        raise HTTPException(status_code=403, detail="回调验签失败或未配置密钥，拒收")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=422, detail="回调报文格式不正确")
    order = await db.get(PhoneCallOrder, int(payload.get("order_id") or 0))
    if order is None:
        raise HTTPException(status_code=404, detail="工单不存在（查无原状态）")
    # 幂等 / 原状态优先：终态或已在目标状态 → 返回现状不重复应用
    outcome = str(payload.get("outcome", ""))
    mapped = {
        "no_answer": PhoneOrderStatus.QUEUED.value,
        "busy": PhoneOrderStatus.QUEUED.value,
        "wrong_number": PhoneOrderStatus.WRONG_NUMBER.value,
        "refused": PhoneOrderStatus.REFUSED.value,
        "completed": PhoneOrderStatus.COMPLETED.value,
    }
    if order.status in ("cancelled", "failed", "completed", "wrong_number", "refused"):
        return {"order_id": order.id, "status": order.status, "applied": False, "detail": "工单已终态，保持原状态"}
    if outcome not in mapped:
        raise HTTPException(status_code=422, detail="未知回调结果")
    receipt_ref = str(payload.get("receipt_ref") or "")[:200] or None
    values: dict[str, Any] = {"status": mapped[outcome]}
    if receipt_ref:
        values["receipt_ref"] = receipt_ref
    if outcome in ("no_answer", "busy"):
        new_attempts = (order.attempts or 0) + 1
        values["attempts"] = new_attempts
        values["next_retry_at"] = now_utc() + retry_delay()
        if new_attempts >= max_attempts():
            values["status"] = PhoneOrderStatus.FAILED.value
    await db.execute(update(PhoneCallOrder).where(PhoneCallOrder.id == order.id).values(**values))
    db.add(
        PhoneCallAttempt(
            order_id=order.id, outcome=outcome, actor="system", simulated=False,
            receipt_ref=receipt_ref, detail={"callback": True}, started_at=now_utc(),
            finished_at=now_utc(),
        )
    )
    await audit(
        db, action="phone.callback", resource_type="phone_call_order", resource_id=order.id,
        detail={"outcome": outcome, "receipt": bool(receipt_ref)}, actor_type="system",
    )
    await db.commit()
    return {"order_id": order.id, "status": values["status"], "applied": True}


# ───────────────────────── C 端状态 ─────────────────────────


async def user_phone_status(db: AsyncSession, user: User) -> dict:
    """本人电话通路状态：三类授权现状（条款未发布时 terms_available=False）、
    联系人（掩码）、近期工单状态（不含通话内容与回执）。"""
    from app.services.steward_service import consent_records

    records = {r["purpose"]: r for r in await consent_records(db, user)}
    contacts = (
        (
            await db.execute(
                select(PhoneContact).where(PhoneContact.user_id == user.id).order_by(PhoneContact.id.desc())
            )
        )
        .scalars()
        .all()
    )
    orders = (
        (
            await db.execute(
                select(PhoneCallOrder)
                .where(PhoneCallOrder.user_id == user.id)
                .order_by(PhoneCallOrder.id.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return {
        "consents": [
            records.get(purpose) or {"purpose": purpose, "granted": False, "outdated": False,
                                     "terms_available": False}
            for purpose in (
                CONSENT_PURPOSE_STEWARD_PHONE_OUTREACH,
                "steward_contact_outreach",
                CONSENT_PURPOSE_STEWARD_CALL_RECORDING,
            )
        ],
        "contacts": [
            {
                "id": c.id, "name": c.name, "relation": c.relation,
                "phone_masked": mask_phone(c.phone), "consent_status": c.consent_status,
            }
            for c in contacts
        ],
        "orders": [
            {"id": o.id, "purpose": o.purpose, "target_type": o.target_type,
             "status": o.status, "created_at": o.created_at}
            for o in orders
        ],
        "ccc_ready": adapter_ready(),
        "note": NO_CCC_NOTE if not adapter_ready() else "CCC 通路已配置",
    }
