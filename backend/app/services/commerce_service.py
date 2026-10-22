"""服务包交易域业务层：商品可售性、订单与支付入账、积分账本、权益、批次、退款。

事务约定：跨多表的不变量操作（下单+扣分+发权益、回调入账、批次确认、退款结论）
在本层内一次性 commit / rollback；路由层只做鉴权、参数与序列化。
并发防护：
- 订单幂等 UNIQUE(user_id, client_order_id)，插入冲突后按幂等语义读回；
- 支付入账 / 认领 / 批次确认均为条件 UPDATE + 行锁（with_for_update），重复请求返回同一结果；
- 积分扣减锁账户行且 UPDATE 带 balance >= :cost 条件，余额不足映射 409。
"""

import hashlib
import re
import secrets
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.common import as_utc
from app.models import (
    BatchRowStatus,
    BatchStatus,
    EntitlementSource,
    EntitlementStatus,
    MpBatchRow,
    MpEntitlement,
    MpOrder,
    MpOrg,
    MpOrgAccount,
    MpPackage,
    MpPackageVersion,
    MpPayment,
    MpPaymentEvent,
    MpPointsAccount,
    MpPointsEntry,
    MpPurchaseBatch,
    MpRefund,
    MpUserConsent,
    OrderStatus,
    PackageStatus,
    PointsBizType,
    RefundStatus,
    User,
)
from app.services import wxpay_service

SHANGHAI = ZoneInfo("Asia/Shanghai")
_CN_PHONE_RE = re.compile(r"1[3-9]\d{9}")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def gen_no(prefix: str) -> str:
    return f"{prefix}{datetime.now(SHANGHAI):%Y%m%d}{secrets.token_hex(4).upper()}"


def normalize_phone(raw: str) -> str | None:
    """名单行手机号规范化：去空白/连字符/句点，去 +86 前缀；非法返回 None。"""
    value = re.sub(r"[\s\-.()]", "", raw or "")
    if value.startswith("+86"):
        value = value[3:]
    if _CN_PHONE_RE.fullmatch(value):
        return value
    return None


def valid_until_from(activated_at: datetime, validity_days: int) -> datetime:
    """权益有效期：按 Asia/Shanghai 自然日，激活日 + validity_days 的当日 23:59:59（转回 UTC）。"""
    local_day = activated_at.astimezone(SHANGHAI).date() + timedelta(days=validity_days)
    end_local = datetime.combine(local_day, time(23, 59, 59), tzinfo=SHANGHAI)
    return end_local.astimezone(timezone.utc)


# ───────────────────────── 商品可售性 ─────────────────────────


def version_blockers(version: MpPackageVersion | None) -> list[str]:
    """版本配置完整性；上架与下单都以 blockers 为空为前提。"""
    if version is None:
        return ["尚未创建任何版本"]
    blockers: list[str] = []
    if version.price_cents <= 0:
        blockers.append("价格未设置")
    if not version.benefits:
        blockers.append("权益项目为空")
    if version.validity_days <= 0:
        blockers.append("有效期未设置")
    if not (version.terms or "").strip() or not (version.service_desc or "").strip():
        blockers.append("公开说明或售后规则未填写")
    return blockers


async def get_package_with_version(
    db: AsyncSession, package_id: int, *, for_update: bool = False
) -> tuple[MpPackage, MpPackageVersion | None]:
    stmt = select(MpPackage).where(MpPackage.id == package_id)
    if for_update:
        stmt = stmt.with_for_update()
    package = (await db.execute(stmt)).scalar_one_or_none()
    if package is None:
        raise HTTPException(status_code=404, detail="服务包不存在")
    version = None
    if package.current_version_id is not None:
        version = await db.get(MpPackageVersion, package.current_version_id)
    return package, version


def purchasable_flags(
    version: MpPackageVersion | None, *, blockers: list[str], wxpay_enabled: bool, balance: int
) -> dict:
    reasons = list(blockers)
    wxpay = not blockers and wxpay_enabled
    points = not blockers and version is not None and version.points_cost is not None
    if not blockers and not wxpay_enabled:
        reasons.append("支付通道未开通")
    if not blockers and (version is None or version.points_cost is None):
        reasons.append("该服务包不支持积分兑换")
    return {"wxpay": wxpay, "points": points, "reasons": reasons}


# ───────────────────────── 积分账本 ─────────────────────────


async def get_points_balance(db: AsyncSession, user_id: int) -> int:
    account = await db.get(MpPointsAccount, user_id)
    return int(account.balance) if account else 0


async def _locked_account(db: AsyncSession, user_id: int, *, create: bool = True) -> MpPointsAccount:
    account = (
        await db.execute(
            select(MpPointsAccount).where(MpPointsAccount.user_id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    if account is None and create:
        account = MpPointsAccount(user_id=user_id, balance=0)
        db.add(account)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="积分账户创建冲突，请重试")
    if account is None:
        raise HTTPException(status_code=404, detail="积分账户不存在")
    return account


async def apply_points(
    db: AsyncSession,
    *,
    user_id: int,
    delta: int,
    biz_type: str,
    order_id: int | None = None,
    note: str | None = None,
    admin_id: int | None = None,
) -> MpPointsEntry:
    """追加一条流水并更新余额；负数变动余额不足映射 409。

    必须在调用方事务内执行；UNIQUE(biz_type, order_id) 由数据库兜底防同单重复。
    """
    if delta == 0:
        raise HTTPException(status_code=422, detail="积分变动不能为 0")
    account = await _locked_account(db, user_id)
    if delta < 0 and account.balance + delta < 0:
        raise HTTPException(status_code=409, detail="积分余额不足")
    guard = [MpPointsAccount.user_id == user_id]
    if delta < 0:
        guard.append(MpPointsAccount.balance >= -delta)
    result = await db.execute(
        update(MpPointsAccount)
        .where(*guard)
        .values(balance=MpPointsAccount.balance + delta)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="积分余额不足")
    # synchronize_session 会回写对象，但仍显式刷新一次，保证余额取自数据库
    await db.refresh(account)
    entry = MpPointsEntry(
        user_id=user_id,
        delta=delta,
        balance_after=account.balance,
        biz_type=biz_type,
        order_id=order_id,
        note=note,
        operated_by_admin_id=admin_id,
    )
    db.add(entry)
    await db.flush()
    return entry


# ───────────────────────── 订单 ─────────────────────────


async def _record_consent(
    db: AsyncSession, user: User, order: MpOrder, version: MpPackageVersion
) -> None:
    db.add(
        MpUserConsent(
            user_id=user.id,
            purpose="package_purchase",
            version=f"v{version.version_no}",
            text=version.terms,
            order_id=order.id,
        )
    )


async def create_order(
    db: AsyncSession,
    user: User,
    *,
    client_order_id: str,
    package_id: int,
    pay_type: str,
    agree_terms: bool,
) -> tuple[MpOrder, bool]:
    """创建订单。返回 (订单, 是否新建)；幂等命中返回 (既有订单, False)。

    points 单在同一事务内完成：扣分 → paid → 发权益 → 授权快照。
    """
    if not agree_terms:
        raise HTTPException(status_code=422, detail="请先阅读并同意购买协议与售后规则")

    # rollback 会过期会话内对象，先取出标量避免事后懒加载
    buyer_id = user.id
    orderer_phone = user.phone

    # 幂等优先于可售性/通道检查：已存在的单号在任何后续状态下都返回原订单或 409，
    # 避免商品临时下架或通道停用时丢失幂等响应
    existing = (
        await db.execute(
            select(MpOrder).where(
                MpOrder.user_id == buyer_id, MpOrder.client_order_id == client_order_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.package_id != package_id or existing.pay_type != pay_type:
            raise HTTPException(status_code=409, detail="该客户端单号已用于其他订单参数")
        await _lazy_close(db, existing)
        return existing, False

    package, version = await get_package_with_version(db, package_id, for_update=True)
    if package.status != PackageStatus.ON_SHELF.value:
        raise HTTPException(status_code=409, detail="该服务包当前不可购买")
    blockers = version_blockers(version)
    if blockers:
        raise HTTPException(status_code=409, detail="服务包配置不完整，暂不可购买")

    if pay_type == "wxpay" and not wxpay_service.wxpay_enabled():
        raise HTTPException(status_code=503, detail="支付通道未开通，请联系客服")
    if pay_type == "points":
        if version.points_cost is None:
            raise HTTPException(status_code=409, detail="该服务包不支持积分兑换")

    order = MpOrder(
        order_no=gen_no("SP"),
        user_id=user.id,
        client_order_id=client_order_id,
        package_id=package.id,
        package_version_id=version.id,
        package_name=package.name,
        version_no=version.version_no,
        pay_type=pay_type,
        total_cents=version.price_cents,
        points_cost=version.points_cost if pay_type == "points" else None,
        status=OrderStatus.PENDING_PAYMENT.value,
        terms_version=f"v{version.version_no}",
        expires_at=(
            now_utc() + timedelta(minutes=settings.COMMERCE_ORDER_TTL_MINUTES)
            if pay_type == "wxpay"
            else None
        ),
    )
    db.add(order)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(MpOrder).where(
                    MpOrder.user_id == buyer_id, MpOrder.client_order_id == client_order_id
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=409, detail="订单创建冲突，请重试")
        return existing, False

    await _record_consent(db, user, order, version)

    if pay_type == "points":
        await apply_points(
            db,
            user_id=buyer_id,
            delta=-int(version.points_cost),
            biz_type=PointsBizType.REDEEM.value,
            order_id=order.id,
            note=f"兑换服务包 {package.name} v{version.version_no}",
        )
        order.status = OrderStatus.PAID.value
        order.paid_at = now_utc()
        await issue_order_entitlement(db, order, version)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(MpOrder).where(
                    MpOrder.user_id == buyer_id, MpOrder.client_order_id == client_order_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
        raise HTTPException(status_code=409, detail="订单创建冲突，请重试")
    await db.refresh(order)
    return order, True


async def _lazy_close(db: AsyncSession, order: MpOrder) -> None:
    """读取路径上的懒超时：待支付订单过期即置 closed（支付成功晚到仍可入账）。"""
    if (
        order.status == OrderStatus.PENDING_PAYMENT.value
        and order.expires_at is not None
        and as_utc(order.expires_at) <= now_utc()
    ):
        await close_order_by_user(db, order, reason="timeout")


async def close_order_by_user(db: AsyncSession, order: MpOrder, *, reason: str) -> None:
    result = await db.execute(
        update(MpOrder)
        .where(MpOrder.id == order.id, MpOrder.status == OrderStatus.PENDING_PAYMENT.value)
        .values(status=OrderStatus.CLOSED.value, closed_at=now_utc())
    )
    if result.rowcount == 1:
        if reason == "user" and order.pay_type == "wxpay":
            cfg = wxpay_service.load_config()
            if cfg is not None:
                try:
                    await wxpay_service.close_order(cfg, order.order_no)
                except wxpay_service.WxPayError:
                    pass  # 微信关单失败不阻断本地关闭；未付订单不会入账重复
        db.add(
            MpPaymentEvent(
                order_id=order.id,
                out_trade_no=order.order_no,
                event_type="query_sync",
                result="closed",
                detail={"reason": reason},
            )
        )
        await db.commit()
        await db.refresh(order)
    else:
        await db.refresh(order)


async def create_prepay(db: AsyncSession, user: User, order: MpOrder) -> MpPayment:
    """微信 JSAPI 预下单；每次调用新建一条 created 支付记录。"""
    await _lazy_close(db, order)
    if order.status != OrderStatus.PENDING_PAYMENT.value:
        raise HTTPException(status_code=409, detail="订单当前状态不可支付")
    if order.pay_type != "wxpay":
        raise HTTPException(status_code=409, detail="该订单不是微信支付订单")
    cfg = wxpay_service.load_config()
    if cfg is None:
        raise HTTPException(status_code=503, detail="支付通道未开通，请联系客服")
    try:
        prepay_id = await wxpay_service.jsapi_prepay(
            cfg,
            out_trade_no=order.order_no,
            description=f"{order.package_name} v{order.version_no}",
            total_cents=order.total_cents,
            openid=user.wx_openid or "",
        )
    except wxpay_service.WxPayError as exc:
        payment = MpPayment(
            order_id=order.id,
            status="failed",
            amount_cents=order.total_cents,
            fail_reason=str(exc)[:200],
        )
        db.add(payment)
        await db.commit()
        raise HTTPException(status_code=502, detail="微信支付下单失败，请稍后重试")
    payment = MpPayment(
        order_id=order.id, prepay_id=prepay_id, status="created", amount_cents=order.total_cents
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


# ───────────────────────── 支付入账（回调 / 查单共用） ─────────────────────────


async def issue_order_entitlement(
    db: AsyncSession, order: MpOrder, version: MpPackageVersion
) -> MpEntitlement:
    """为订单发一份 active 权益；UNIQUE(order_id) 兜底，重复插入读回原记录。"""
    activated = now_utc()
    entitlement = MpEntitlement(
        source_type=EntitlementSource.ORDER.value,
        order_id=order.id,
        user_id=order.user_id,
        package_id=version.package_id,
        package_version_id=version.id,
        package_name=order.package_name,
        version_no=version.version_no,
        benefits=version.benefits,
        validity_days=version.validity_days,
        status=EntitlementStatus.ACTIVE.value,
        activated_at=activated,
        valid_until=valid_until_from(activated, version.validity_days),
    )
    db.add(entitlement)
    try:
        await db.flush()
        return entitlement
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(MpEntitlement).where(MpEntitlement.order_id == order.id)
            )
        ).scalar_one()
        return existing


async def mark_order_paid(
    db: AsyncSession, order: MpOrder, *, transaction_id: str, event_type: str = "notify_accepted"
) -> bool:
    """条件更新为 paid；成功才发权益与写事件。已 paid 返回 False（幂等重放）。"""
    result = await db.execute(
        update(MpOrder)
        .where(
            MpOrder.id == order.id,
            MpOrder.status.in_([OrderStatus.PENDING_PAYMENT.value, OrderStatus.CLOSED.value]),
        )
        .values(status=OrderStatus.PAID.value, paid_at=now_utc(), transaction_id=transaction_id)
    )
    if result.rowcount != 1:
        return False
    version = await db.get(MpPackageVersion, order.package_version_id)
    entitlement = await issue_order_entitlement(db, order, version)
    synced = await db.execute(
        update(MpPayment)
        .where(
            MpPayment.order_id == order.id,
            MpPayment.status == "created",
            MpPayment.amount_cents == order.total_cents,
        )
        .values(status="success", transaction_id=transaction_id)
    )
    if synced.rowcount == 0:
        # 没有可勾销的预下单记录（如回调先于 prepay 记录落库）也要留下成功流水
        db.add(
            MpPayment(
                order_id=order.id,
                transaction_id=transaction_id,
                status="success",
                amount_cents=order.total_cents,
            )
        )
        await db.flush()
    db.add(
        MpPaymentEvent(
            order_id=order.id,
            out_trade_no=order.order_no,
            transaction_id=transaction_id,
            event_type=event_type,
            result="paid",
            detail={"entitlement_id": entitlement.id},
        )
    )
    await db.commit()
    await db.refresh(order)
    return True


def _payment_event(
    *, order: MpOrder | None, out_trade_no: str | None, event_type: str, result: str, detail: dict | None
) -> MpPaymentEvent:
    return MpPaymentEvent(
        order_id=order.id if order else None,
        out_trade_no=out_trade_no,
        transaction_id=None,
        event_type=event_type,
        result=result,
        detail=detail,
    )


async def process_payment_result(
    db: AsyncSession,
    *,
    out_trade_no: str,
    transaction_id: str | None,
    trade_state: str,
    amount_total: int | None,
    event_type: str,
) -> str:
    """回调 / 查单共用的入账判定。返回 accepted / replay / amount_mismatch /
    unknown_order / failed_state / not_success；调用方据此映射响应与审计。

    detail 不含手机号或密钥；金额只记分值。
    """
    order = (
        await db.execute(
            select(MpOrder).where(MpOrder.order_no == out_trade_no).with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        db.add(
            _payment_event(
                order=None,
                out_trade_no=out_trade_no,
                event_type=event_type if event_type != "query_sync" else "query_sync",
                result="unknown_order",
                detail={"trade_state": trade_state},
            )
        )
        await db.commit()
        return "unknown_order"

    if trade_state == "SUCCESS":
        if amount_total is not None and int(amount_total) != order.total_cents:
            db.add(
                _payment_event(
                    order=order,
                    out_trade_no=out_trade_no,
                    event_type=event_type,
                    result="amount_mismatch",
                    detail={"expected": order.total_cents, "got": int(amount_total)},
                )
            )
            await db.commit()
            return "amount_mismatch"
        if order.status == OrderStatus.PAID.value:
            db.add(
                _payment_event(
                    order=order,
                    out_trade_no=out_trade_no,
                    event_type=event_type,
                    result="replay",
                    detail={"transaction_id": transaction_id},
                )
            )
            await db.commit()
            return "replay"
        accepted = await mark_order_paid(
            db, order, transaction_id=transaction_id or "", event_type=event_type
        )
        return "accepted" if accepted else "replay"

    # 非 SUCCESS：登记最新预下单记录为失败，订单状态不变
    await db.execute(
        update(MpPayment)
        .where(MpPayment.order_id == order.id, MpPayment.status == "created")
        .values(status="failed", fail_reason=f"trade_state={trade_state}"[:200])
    )
    db.add(
        _payment_event(
            order=order,
            out_trade_no=out_trade_no,
            event_type=event_type,
            result=f"trade_state_{trade_state}",
            detail={"trade_state": trade_state},
        )
    )
    await db.commit()
    return "not_success"


# ───────────────────────── 权益（认领 / 拒绝） ─────────────────────────


def entitlement_visible_to(user: User, entitlement: MpEntitlement) -> bool:
    if entitlement.user_id == user.id:
        return True
    return bool(user.phone) and entitlement.phone == user.phone and entitlement.status in (
        EntitlementStatus.PENDING.value,
        EntitlementStatus.REJECTED.value,
    )


async def claim_entitlement(db: AsyncSession, user: User, entitlement: MpEntitlement) -> MpEntitlement:
    if entitlement.source_type != EntitlementSource.BATCH.value:
        if entitlement.user_id == user.id and entitlement.status == EntitlementStatus.ACTIVE.value:
            return entitlement
        raise HTTPException(status_code=404, detail="权益不存在")
    if not user.phone:
        raise HTTPException(status_code=404, detail="权益不存在")
    activated = now_utc()
    result = await db.execute(
        update(MpEntitlement)
        .where(
            MpEntitlement.id == entitlement.id,
            MpEntitlement.phone == user.phone,
            MpEntitlement.status.in_(
                [EntitlementStatus.PENDING.value, EntitlementStatus.REJECTED.value]
            ),
        )
        .values(
            status=EntitlementStatus.ACTIVE.value,
            user_id=user.id,
            activated_at=activated,
            valid_until=valid_until_from(activated, entitlement.validity_days),
            rejected_at=None,
        )
    )
    if result.rowcount == 1:
        await db.commit()
        await db.refresh(entitlement)
        return entitlement
    # 条件更新未命中（并发重复请求）：无未提交变更，禁止 rollback——
    # rollback 会过期会话内全部对象（含 user），后续属性访问将触发懒加载 IO
    await db.refresh(entitlement)
    if entitlement.user_id == user.id and entitlement.status == EntitlementStatus.ACTIVE.value:
        return entitlement  # 并发重复请求返回同一结果
    if entitlement.status == EntitlementStatus.VOID.value:
        raise HTTPException(status_code=409, detail="该权益已作废")
    raise HTTPException(status_code=404, detail="权益不存在")


async def reject_entitlement(db: AsyncSession, user: User, entitlement: MpEntitlement) -> MpEntitlement:
    if entitlement.source_type != EntitlementSource.BATCH.value:
        raise HTTPException(status_code=404, detail="权益不存在")
    if not user.phone or entitlement.phone != user.phone:
        raise HTTPException(status_code=404, detail="权益不存在")
    if entitlement.status == EntitlementStatus.REJECTED.value:
        return entitlement
    if entitlement.status != EntitlementStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="该权益当前状态不可拒绝")
    result = await db.execute(
        update(MpEntitlement)
        .where(
            MpEntitlement.id == entitlement.id,
            MpEntitlement.status == EntitlementStatus.PENDING.value,
        )
        .values(status=EntitlementStatus.REJECTED.value, rejected_at=now_utc())
    )
    if result.rowcount == 1:
        await db.commit()
        await db.refresh(entitlement)
        return entitlement
    await db.refresh(entitlement)
    return entitlement


# ───────────────────────── 单位批次 ─────────────────────────


async def preview_batch(
    db: AsyncSession,
    *,
    uploaded_by_id: int,
    org_id: int,
    package_id: int,
    version_id: int,
    quantity: int,
    file_name: str,
    raw_rows: list[str],
) -> tuple[MpPurchaseBatch, list[MpBatchRow], dict]:
    """预览导入：逐行判定，不产生任何权益。"""
    org = await db.get(MpOrg, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="采购单位不存在")
    package = await db.get(MpPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="服务包不存在")
    version = await db.get(MpPackageVersion, version_id)
    if version is None or version.package_id != package.id:
        raise HTTPException(status_code=404, detail="服务包版本不存在")

    # 该单位同版本已有非 void 权益的手机号集合（跨批次防重复发放）
    existing_phones = set(
        (
            await db.execute(
                select(MpEntitlement.phone)
                .join(MpPurchaseBatch, MpEntitlement.batch_id == MpPurchaseBatch.id)
                .where(
                    MpPurchaseBatch.org_id == org_id,
                    MpPurchaseBatch.package_version_id == version.id,
                    MpEntitlement.status != EntitlementStatus.VOID.value,
                    MpEntitlement.phone.is_not(None),
                )
            )
        ).scalars()
    )

    normalized_rows: list[tuple[int, str]] = []
    seen: set[str] = set()
    valid_so_far = 0
    row_defs: list[MpBatchRow] = []
    for idx, raw in enumerate(raw_rows, start=1):
        phone = normalize_phone(raw)
        if phone is None:
            row_defs.append(
                MpBatchRow(
                    batch_id=0,
                    row_no=idx,
                    phone=raw.strip()[:20] or "(空)",
                    status=BatchRowStatus.FORMAT_ERROR.value,
                    error_detail="手机号格式不正确",
                )
            )
            continue
        normalized_rows.append((idx, phone))
        if phone in seen:
            row_defs.append(
                MpBatchRow(
                    batch_id=0,
                    row_no=idx,
                    phone=phone,
                    status=BatchRowStatus.DUPLICATE.value,
                    error_detail="与本批次前面的行重复",
                )
            )
            continue
        if phone in existing_phones:
            row_defs.append(
                MpBatchRow(
                    batch_id=0,
                    row_no=idx,
                    phone=phone,
                    status=BatchRowStatus.DUPLICATE.value,
                    error_detail="该单位此版本已有该号码的权益",
                )
            )
            continue
        if valid_so_far >= quantity:
            row_defs.append(
                MpBatchRow(
                    batch_id=0,
                    row_no=idx,
                    phone=phone,
                    status=BatchRowStatus.OVER_LIMIT.value,
                    error_detail="超出采购数量",
                )
            )
            continue
        seen.add(phone)
        valid_so_far += 1
        row_defs.append(
            MpBatchRow(batch_id=0, row_no=idx, phone=phone, status=BatchRowStatus.VALID.value)
        )

    digest_input = "\n".join(f"{no}\t{phone}" for no, phone in normalized_rows) or "\n".join(
        r.strip() for r in raw_rows
    )
    file_sha256 = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    batch = MpPurchaseBatch(
        batch_no=gen_no("PB"),
        org_id=org_id,
        package_id=package.id,
        package_version_id=version.id,
        quantity=quantity,
        file_name=file_name,
        file_sha256=file_sha256,
        status=BatchStatus.PREVIEW.value,
        uploaded_by_admin_id=uploaded_by_id,
    )
    db.add(batch)
    await db.flush()
    for row in row_defs:
        row.batch_id = batch.id
        db.add(row)
    await db.commit()
    await db.refresh(batch)

    summary = {
        "total": len(raw_rows),
        "valid": sum(1 for r in row_defs if r.status == BatchRowStatus.VALID.value),
        "duplicate": sum(1 for r in row_defs if r.status == BatchRowStatus.DUPLICATE.value),
        "format_error": sum(1 for r in row_defs if r.status == BatchRowStatus.FORMAT_ERROR.value),
        "over_limit": sum(1 for r in row_defs if r.status == BatchRowStatus.OVER_LIMIT.value),
    }
    return batch, row_defs, summary


async def batch_status_counts(db: AsyncSession, batch_id: int) -> dict[str, int]:
    rows = (
        await db.execute(
            select(MpEntitlement.status, func.count())
            .where(MpEntitlement.batch_id == batch_id)
            .group_by(MpEntitlement.status)
        )
    ).all()
    counts = {status: int(count) for status, count in rows}
    return {
        "issued": sum(counts.values()),
        "claimed": counts.get(EntitlementStatus.ACTIVE.value, 0),
        "rejected": counts.get(EntitlementStatus.REJECTED.value, 0),
        "pending": counts.get(EntitlementStatus.PENDING.value, 0),
    }


async def confirm_batch(
    db: AsyncSession, *, confirmed_by_id: int, batch: MpPurchaseBatch
) -> MpPurchaseBatch:
    """确认批次：仅 preview 可确认一次；为每个 valid 行发 pending 权益。"""
    batch = (
        await db.execute(
            select(MpPurchaseBatch).where(MpPurchaseBatch.id == batch.id).with_for_update()
        )
    ).scalar_one()
    if batch.status != BatchStatus.PREVIEW.value:
        raise HTTPException(status_code=409, detail="该批次已确认或已取消")
    version = await db.get(MpPackageVersion, batch.package_version_id)
    package = await db.get(MpPackage, batch.package_id)
    rows = (
        await db.execute(
            select(MpBatchRow).where(
                MpBatchRow.batch_id == batch.id, MpBatchRow.status == BatchRowStatus.VALID.value
            ).order_by(MpBatchRow.row_no.asc())
        )
    ).scalars().all()
    for row in rows:
        db.add(
            MpEntitlement(
                source_type=EntitlementSource.BATCH.value,
                batch_id=batch.id,
                batch_row_id=row.id,
                phone=row.phone,
                package_id=package.id,
                package_version_id=version.id,
                package_name=package.name,
                version_no=version.version_no,
                benefits=version.benefits,
                validity_days=version.validity_days,
                status=EntitlementStatus.PENDING.value,
            )
        )
    batch.status = BatchStatus.CONFIRMED.value
    batch.confirmed_by_admin_id = confirmed_by_id
    batch.confirmed_at = now_utc()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="批次确认冲突，请刷新后重试")
    await db.refresh(batch)
    return batch


async def cancel_batch(db: AsyncSession, batch: MpPurchaseBatch) -> MpPurchaseBatch:
    result = await db.execute(
        update(MpPurchaseBatch)
        .where(
            MpPurchaseBatch.id == batch.id, MpPurchaseBatch.status == BatchStatus.PREVIEW.value
        )
        .values(status=BatchStatus.CANCELLED.value, cancelled_at=now_utc())
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=409, detail="仅待确认批次可取消")
    await db.commit()
    await db.refresh(batch)
    return batch


# ───────────────────────── 退款 ─────────────────────────


async def request_refund(
    db: AsyncSession,
    *,
    order: MpOrder,
    channel: str,
    amount_cents: int | None,
    reason: str,
    requested_by_admin_id: int,
) -> MpRefund:
    order = (
        await db.execute(select(MpOrder).where(MpOrder.id == order.id).with_for_update())
    ).scalar_one()
    if order.status not in (OrderStatus.PAID.value, OrderStatus.REFUND_FAILED.value):
        raise HTTPException(status_code=409, detail="订单当前状态不可退款")
    if order.pay_type == "points" and channel != "manual":
        raise HTTPException(status_code=409, detail="积分兑换订单退款由人工处理（线下）")
    amount = amount_cents or order.total_cents
    if amount > order.total_cents:
        raise HTTPException(status_code=409, detail="退款金额超过订单金额")
    taken = (
        await db.execute(
            select(func.coalesce(func.sum(MpRefund.amount_cents), 0)).where(
                MpRefund.order_id == order.id,
                MpRefund.status.in_(
                    [RefundStatus.PENDING.value, RefundStatus.PROCESSING.value, RefundStatus.SUCCESS.value]
                ),
            )
        )
    ).scalar() or 0
    if int(taken) + amount > order.total_cents:
        raise HTTPException(status_code=409, detail="累计退款金额超过订单金额")

    refund = MpRefund(
        refund_no=gen_no("RF"),
        order_id=order.id,
        channel=channel,
        status=RefundStatus.PENDING.value,
        amount_cents=amount,
        reason=reason,
        requested_by_admin_id=requested_by_admin_id,
    )
    if channel == "wxpay":
        cfg = wxpay_service.load_config()
        if cfg is None:
            raise HTTPException(status_code=503, detail="支付通道未开通，无法发起线上退款")
        try:
            data = await wxpay_service.create_refund(
                cfg,
                out_trade_no=order.order_no,
                out_refund_no=refund.refund_no,
                refund_cents=amount,
                total_cents=order.total_cents,
                reason=reason,
            )
            refund.status = RefundStatus.PROCESSING.value
            refund.wx_refund_id = str(data.get("refund_id") or "") or None
        except wxpay_service.WxPayError as exc:
            refund.status = RefundStatus.FAILED.value
            refund.note = str(exc)[:200]
    order.status = OrderStatus.REFUND_PENDING.value
    db.add(refund)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="退款申请冲突，请重试")
    await db.refresh(refund)
    return refund


async def resolve_refund(
    db: AsyncSession,
    *,
    refund: MpRefund,
    status: str,
    note: str | None,
    resolved_by_admin_id: int,
) -> MpRefund:
    """人工登记退款结论；success 联动订单 / 权益 / 积分返还，全部在同一事务。"""
    refund = (
        await db.execute(select(MpRefund).where(MpRefund.id == refund.id).with_for_update())
    ).scalar_one()
    order = (
        await db.execute(select(MpOrder).where(MpOrder.id == refund.order_id).with_for_update())
    ).scalar_one()
    if refund.status not in (RefundStatus.PENDING.value, RefundStatus.PROCESSING.value):
        raise HTTPException(status_code=409, detail="该退款已有结论")
    refund.status = RefundStatus.SUCCESS.value if status == "success" else RefundStatus.FAILED.value
    refund.note = note
    refund.resolved_by_admin_id = resolved_by_admin_id
    refund.finished_at = now_utc()

    if status == "success":
        # 订单来源权益作废（原因留痕）；积分单返还扣减的积分
        await db.execute(
            update(MpEntitlement)
            .where(
                MpEntitlement.order_id == order.id,
                MpEntitlement.status != EntitlementStatus.VOID.value,
            )
            .values(status=EntitlementStatus.VOID.value, void_reason="订单已退款", voided_at=now_utc())
        )
        if order.pay_type == "points" and order.points_cost:
            await apply_points(
                db,
                user_id=order.user_id,
                delta=int(order.points_cost),
                biz_type=PointsBizType.REFUND_REVERT.value,
                order_id=order.id,
                note=f"订单 {order.order_no} 退款返还",
            )
        success_sum = int(
            (
                await db.execute(
                    select(func.coalesce(func.sum(MpRefund.amount_cents), 0)).where(
                        MpRefund.order_id == order.id,
                        MpRefund.status == RefundStatus.SUCCESS.value,
                    )
                )
            ).scalar()
            or 0
        )
        order.status = (
            OrderStatus.REFUNDED.value if success_sum >= order.total_cents else OrderStatus.PAID.value
        )
    else:
        order.status = OrderStatus.REFUND_FAILED.value
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="退款结论登记冲突，请重试")
    await db.refresh(refund)
    return refund


# ───────────────────────── 授权记录 ─────────────────────────


async def revoke_consent(db: AsyncSession, user: User, consent: MpUserConsent) -> MpUserConsent:
    if consent.revoked_at is None:
        result = await db.execute(
            update(MpUserConsent)
            .where(MpUserConsent.id == consent.id, MpUserConsent.revoked_at.is_(None))
            .values(revoked_at=now_utc())
        )
        if result.rowcount == 1:
            await db.commit()
    await db.refresh(consent)
    return consent


async def org_batch_guard(db: AsyncSession, *, org_id: int, batch_id: int) -> MpPurchaseBatch:
    """单位账号只允许访问本单位批次；越权一律 404 不泄露存在性。"""
    batch = await db.get(MpPurchaseBatch, batch_id)
    if batch is None or batch.org_id != org_id:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


async def authenticate_org_account(db: AsyncSession, username: str, password: str):
    from app.core.security import verify_password

    account = (
        await db.execute(select(MpOrgAccount).where(MpOrgAccount.username == username))
    ).scalar_one_or_none()
    if account is None or not verify_password(password, account.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if account.status != "active":
        raise HTTPException(status_code=403, detail="账号已停用")
    org = await db.get(MpOrg, account.org_id)
    if org is None or org.status != "active":
        raise HTTPException(status_code=403, detail="单位已停用")
    account.last_login_at = now_utc()
    await db.commit()
    return account, org
