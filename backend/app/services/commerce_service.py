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
from app.schemas.common import as_utc, mask_phone
from app.models import (
    BatchRowStatus,
    BatchStatus,
    EntitlementSource,
    EntitlementStatus,
    MpBatchRow,
    MpEntitlement,
    MpEntitlementEvent,
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
    MpSaleRelease,
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
    version: MpPackageVersion | None,
    *,
    blockers: list[str],
    wxpay_enabled: bool,
    balance: int,
    released: bool = True,
) -> dict:
    """可购判定：配置完整 + 开售放行（R8）+ 支付通道/积分价。"""
    reasons = list(blockers)
    if not released:
        reasons.append("未完成开售放行")
    wxpay = not blockers and released and wxpay_enabled
    points = not blockers and released and version is not None and version.points_cost is not None
    if not blockers and released and not wxpay_enabled:
        reasons.append("支付通道未开通")
    if not blockers and released and (version is None or version.points_cost is None):
        reasons.append("该服务包不支持积分兑换")
    return {"wxpay": wxpay, "points": points, "reasons": reasons}


RELEASE_ENVS = ("dev", "test", "staging", "prod", "all")


async def has_active_release(
    db: AsyncSession, package_id: int, version_id: int | None
) -> bool:
    """当前环境（settings.APP_ENV 或 all）存在未撤销的放行记录即视为已放行。"""
    if version_id is None:
        return False
    envs = [settings.APP_ENV, "all"]
    found = (
        await db.execute(
            select(MpSaleRelease.id)
            .where(
                MpSaleRelease.package_id == package_id,
                MpSaleRelease.version_id == version_id,
                MpSaleRelease.env.in_(envs),
                MpSaleRelease.revoked_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


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
            source="order_confirm",
        )
    )


async def create_order(
    db: AsyncSession,
    user: User,
    *,
    client_order_id: str,
    package_id: int,
    version_id: int,
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
    # R7：下单必须携带用户实际确认的版本，防止静默购买未见过的价格/条款
    if version_id != package.current_version_id:
        raise HTTPException(
            status_code=409,
            detail=f"服务包已更新（当前为 v{version.version_no if version else 0}），请重新确认后下单",
        )
    blockers = version_blockers(version)
    if blockers:
        raise HTTPException(status_code=409, detail="服务包配置不完整，暂不可购买")
    # R8：未放行不可售（微信与积分一致判定，直接 API 提交不能绕过）
    if not await has_active_release(db, package.id, package.current_version_id):
        raise HTTPException(status_code=409, detail="服务包未完成开售放行，暂不可购买")

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
    except (wxpay_service.WxPayError, wxpay_service.WxPayTimeout) as exc:
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
        db.add(
            MpEntitlementEvent(
                entitlement_id=entitlement.id,
                event_type="issued",
                detail={"source": "order", "order_no": order.order_no},
                actor_type="system",
            )
        )
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
    """条件更新为 paid；成功才发权益与写事件。已 paid 返回 False（幂等重放）。

    R2：一笔微信交易号只落一条 success 支付记录——多轮预下单时只勾销最新一条
    created，其余置 failed(superseded) 且不带交易号，避免唯一约束冲突。
    """
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

    created_rows = (
        await db.execute(
            select(MpPayment.id)
            .where(
                MpPayment.order_id == order.id,
                MpPayment.status == "created",
                MpPayment.amount_cents == order.total_cents,
            )
            .order_by(MpPayment.id.desc())
        )
    ).scalars().all()
    if created_rows:
        # 最新一条记 success 并绑定交易号；其余尝试标记为已作废（保留历史，不写交易号）
        await db.execute(
            update(MpPayment)
            .where(MpPayment.id == created_rows[0])
            .values(status="success", transaction_id=transaction_id)
        )
        if len(created_rows) > 1:
            await db.execute(
                update(MpPayment)
                .where(MpPayment.id.in_(created_rows[1:]))
                .values(status="failed", fail_reason="superseded")
            )
    else:
        # 没有可勾销的预下单记录（回调先于 prepay 落库）也要留下成功流水；
        # 交易号唯一约束由 R1 的跨订单预检兜底，此处冲突即回滚整单入账
        db.add(
            MpPayment(
                order_id=order.id,
                transaction_id=transaction_id,
                status="success",
                amount_cents=order.total_cents,
            )
        )
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            return False
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


_WX_TX_ID_RE = re.compile(r"[A-Za-z0-9\-_]{1,64}")


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
    currency: str | None = None,
) -> str:
    """回调 / 查单共用的入账判定。返回 accepted / replay / amount_mismatch /
    invalid_success_fields / transaction_reused / unknown_order / not_success /
    invalid_state；调用方据此映射响应与审计。

    R1：SUCCESS 的成功事实必须字段完整且匹配——金额为正整数分、币种 CNY、
    交易号非空且格式受控；缺失/类型错误/币种不符一律拒绝入账并留脱敏事件，
    订单与权益不变。同一交易号已被其他订单入账时拒绝（transaction_reused）。
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
                event_type=event_type,
                result="unknown_order",
                detail={"trade_state": trade_state},
            )
        )
        await db.commit()
        return "unknown_order"

    if not trade_state:
        db.add(
            _payment_event(
                order=order,
                out_trade_no=out_trade_no,
                event_type=event_type,
                result="invalid_state",
                detail={"reason": "missing_trade_state"},
            )
        )
        await db.commit()
        return "invalid_state"

    if trade_state == "SUCCESS":
        tx = (transaction_id or "").strip()
        problems: list[str] = []
        if not tx:
            problems.append("missing_transaction_id")
        elif not _WX_TX_ID_RE.fullmatch(tx):
            problems.append("malformed_transaction_id")
        if isinstance(amount_total, bool) or not isinstance(amount_total, int):
            problems.append("missing_or_invalid_amount")
        elif amount_total <= 0:
            problems.append("non_positive_amount")
        if currency is not None and currency != "CNY":
            problems.append("currency_mismatch")
        if problems:
            db.add(
                _payment_event(
                    order=order,
                    out_trade_no=out_trade_no,
                    event_type=event_type,
                    result="invalid_success_fields",
                    detail={"problems": problems},
                )
            )
            await db.commit()
            return "invalid_success_fields"
        if amount_total != order.total_cents:
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
        # 跨订单交易号防重：同一微信交易号只能为一张订单入账
        reused = (
            await db.execute(
                select(MpPayment.order_id).where(MpPayment.transaction_id == tx)
            )
        ).scalar_one_or_none()
        if reused is not None and reused != order.id:
            db.add(
                _payment_event(
                    order=order,
                    out_trade_no=out_trade_no,
                    event_type=event_type,
                    result="transaction_reused",
                    detail={"transaction_id": tx},
                )
            )
            await db.commit()
            return "transaction_reused"
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
        accepted = await mark_order_paid(db, order, transaction_id=tx, event_type=event_type)
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
        was_rejected = entitlement.status == EntitlementStatus.REJECTED.value
        db.add(
            MpEntitlementEvent(
                entitlement_id=entitlement.id,
                event_type="reclaimed" if was_rejected else "claimed",
                detail={"source": "batch"},
                actor_type="user",
                actor_id=user.id,
            )
        )
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
        db.add(
            MpEntitlementEvent(
                entitlement_id=entitlement.id,
                event_type="rejected",
                detail={"source": "batch"},
                actor_type="user",
                actor_id=user.id,
            )
        )
        await db.commit()
        await db.refresh(entitlement)
        return entitlement
    await db.refresh(entitlement)
    return entitlement


# ───────────────────────── 单位批次 ─────────────────────────


def parse_batch_file(content: bytes, *, max_rows: int) -> list[str]:
    """解析名单原件（I2）：UTF-8 文本，每行取首列（支持逗号/制表符/分号），
    跳过空行与「手机号/phone」表头；返回原始行字符串（规范化在 preview 内统一做）。
    编码错误/超行数抛 422。"""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="名单文件仅支持 UTF-8 文本（CSV/TXT），请另存为 UTF-8 后重试")
    rows: list[str] = []
    for idx, line in enumerate(text.splitlines()):
        cell = line.split(",")[0].split("\t")[0].split(";")[0].strip()
        if not cell:
            continue
        if idx == 0 and cell.lower() in ("手机号", "phone", "mobile", "电话", "号码"):
            continue
        rows.append(cell)
        if len(rows) > max_rows:
            raise HTTPException(status_code=422, detail=f"名单行数超过上限 {max_rows}")
    if not rows:
        raise HTTPException(status_code=422, detail="名单文件中没有可识别的手机号行")
    return rows


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
    file_digest: str | None = None,
    stored_filename: str | None = None,
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

    normalized_input = "\n".join(f"{no}\t{phone}" for no, phone in normalized_rows) or "\n".join(
        r.strip() for r in raw_rows
    )
    normalized_sha256 = hashlib.sha256(normalized_input.encode("utf-8")).hexdigest()
    # 有原件时 file_sha256 记原文件字节摘要；JSON 预览无原件则记规范化摘要（口径在契约注明）
    file_sha256 = file_digest or normalized_sha256

    batch = MpPurchaseBatch(
        batch_no=gen_no("PB"),
        org_id=org_id,
        package_id=package.id,
        package_version_id=version.id,
        quantity=quantity,
        file_name=file_name,
        file_sha256=file_sha256,
        normalized_sha256=normalized_sha256,
        stored_filename=stored_filename,
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
    """确认批次：仅 preview 可确认一次；为每个 valid 行发 pending 权益。

    R6：发放前在确认事务内复查跨批次重复（同单位×版本×手机号非 void 权益），
    数据库层另由 uq_mp_entitlements_batch_phone（PG 部分唯一索引 / SQLite 触发器）
    兜底；任一行冲突则整批回滚 409，不静默少发。
    """
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

    conflicts: list[str] = []
    for row in rows:
        exists = (
            await db.execute(
                select(func.count())
                .select_from(MpEntitlement)
                .where(
                    MpEntitlement.source_type == EntitlementSource.BATCH.value,
                    MpEntitlement.org_id == batch.org_id,
                    MpEntitlement.package_version_id == version.id,
                    MpEntitlement.phone == row.phone,
                    MpEntitlement.status != EntitlementStatus.VOID.value,
                )
            )
        ).scalar()
        if int(exists or 0) > 0:
            conflicts.append(mask_phone(row.phone) or row.phone)
    if conflicts:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"与其他批次存在重复手机号（{', '.join(conflicts[:5])}"
            f"{'…' if len(conflicts) > 5 else ''}），请取消本批并重新预览导入",
        )

    for row in rows:
        db.add(
            MpEntitlement(
                source_type=EntitlementSource.BATCH.value,
                batch_id=batch.id,
                batch_row_id=row.id,
                org_id=batch.org_id,
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
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="确认与其他批次冲突（同单位同版本同手机号），请取消本批并重新预览",
        )
    # 发放事件（issued）在权益 flush 拿到主键后补写
    issued_ids = (
        await db.execute(
            select(MpEntitlement.id).where(
                MpEntitlement.batch_id == batch.id,
                MpEntitlement.batch_row_id.is_not(None),
            )
        )
    ).scalars().all()
    for ent_id in issued_ids:
        db.add(
            MpEntitlementEvent(
                entitlement_id=ent_id,
                event_type="issued",
                detail={"source": "batch", "batch_no": batch.batch_no},
                actor_type="admin",
                actor_id=confirmed_by_id,
            )
        )
    await db.commit()
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
    """登记退款意图 →（wxpay）外部受理 → 回写结果。

    R4：本地先持久化退款单（含稳定退款单号）并提交，再调用微信；同一订单存在
    pending/processing 退款时复用原单（同参数幂等返回，不同参数 409），外部
    受理后本地更新失败也有已落库的退款单号可查证恢复。
    R3：微信明确拒绝 → refund failed + 订单 refund_failed（可重新发起）；
    网络超时/断连 → 外部结果未知，保持 processing 待按单号查证，不当作失败。
    R5：未确认部分退款规则，仅允许全额退款；部分金额返回 409 阻断。
    """
    order = (
        await db.execute(select(MpOrder).where(MpOrder.id == order.id).with_for_update())
    ).scalar_one()
    amount = amount_cents or order.total_cents

    # 同一退款意图复用先于订单状态检查：处理中退款单的重复请求幂等返回原单号
    in_flight = (
        await db.execute(
            select(MpRefund)
            .where(
                MpRefund.order_id == order.id,
                MpRefund.status.in_([RefundStatus.PENDING.value, RefundStatus.PROCESSING.value]),
            )
            .order_by(MpRefund.id.desc())
        )
    ).scalars().first()
    if in_flight is not None:
        if in_flight.channel == channel and in_flight.amount_cents == amount:
            return in_flight  # 幂等：重复请求复用原退款单号
        raise HTTPException(status_code=409, detail="该订单已有处理中的退款单，请先查证或登记其结论")

    if order.status not in (OrderStatus.PAID.value, OrderStatus.REFUND_FAILED.value):
        raise HTTPException(status_code=409, detail="订单当前状态不可退款")
    if order.pay_type == "points" and channel != "manual":
        raise HTTPException(status_code=409, detail="积分兑换订单退款由人工处理（线下）")
    if amount > order.total_cents:
        raise HTTPException(status_code=409, detail="退款金额超过订单金额")
    if amount != order.total_cents:
        raise HTTPException(
            status_code=409,
            detail="部分退款规则尚未确认，当前仅支持全额退款；部分退款需求请走人工客服流程",
        )
    taken = (
        await db.execute(
            select(func.coalesce(func.sum(MpRefund.amount_cents), 0)).where(
                MpRefund.order_id == order.id,
                MpRefund.status == RefundStatus.SUCCESS.value,
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
    db.add(refund)
    order.status = OrderStatus.REFUND_PENDING.value
    try:
        await db.commit()  # 意图先行：外部调用前退款单号已落库
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="退款申请冲突，请重试")

    if channel == "wxpay":
        cfg = wxpay_service.load_config()
        if cfg is None:
            refund.status = RefundStatus.FAILED.value
            refund.note = "支付通道未配置，请改人工渠道或先完成配置"
            order.status = OrderStatus.REFUND_FAILED.value
            await db.commit()
            await db.refresh(refund)
            return refund
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
        except wxpay_service.WxPayTimeout as exc:
            # 结果未知：保持 processing，等待按单号查证（query_refund_status）
            refund.status = RefundStatus.PROCESSING.value
            refund.note = f"外部受理结果未知，待查证：{str(exc)[:120]}"
        except wxpay_service.WxPayError as exc:
            # 明确拒绝：退款失败、订单回到可重试状态
            refund.status = RefundStatus.FAILED.value
            refund.note = str(exc)[:200]
            order.status = OrderStatus.REFUND_FAILED.value
        await db.commit()
    await db.refresh(refund)
    return refund


async def _apply_refund_success(
    db: AsyncSession,
    *,
    refund: MpRefund,
    order: MpOrder,
    basis: str,
    resolved_by_admin_id: int | None,
) -> None:
    """退款成功结论的联动：权益作废（一次）+ 积分返还（一次）+ 订单状态。

    basis 记录结论来源（人工登记 / 微信查单），供审计；积分返还的幂等由
    UNIQUE(biz_type, order_id) 保证，冲突视为已返还（历史结论重复登记）。
    """
    refunded_rows = (
        await db.execute(
            update(MpEntitlement)
            .where(
                MpEntitlement.order_id == order.id,
                MpEntitlement.status != EntitlementStatus.VOID.value,
            )
            .values(status=EntitlementStatus.VOID.value, void_reason="订单已退款", voided_at=now_utc())
        )
    )
    if refunded_rows.rowcount:
        for ent_id in (
            await db.execute(
                select(MpEntitlement.id).where(
                    MpEntitlement.order_id == order.id,
                    MpEntitlement.status == EntitlementStatus.VOID.value,
                    MpEntitlement.void_reason == "订单已退款",
                )
            )
        ).scalars():
            db.add(
                MpEntitlementEvent(
                    entitlement_id=ent_id,
                    event_type="voided",
                    detail={"reason": "订单已退款", "refund_no": refund.refund_no, "basis": basis},
                    actor_type="admin" if resolved_by_admin_id else "system",
                    actor_id=resolved_by_admin_id,
                )
            )
    if order.pay_type == "points" and order.points_cost:
        try:
            await apply_points(
                db,
                user_id=order.user_id,
                delta=int(order.points_cost),
                biz_type=PointsBizType.REFUND_REVERT.value,
                order_id=order.id,
                note=f"订单 {order.order_no} 退款返还",
            )
        except IntegrityError:
            await db.rollback()
            # 同单返还流水已存在：历史结论重复登记，跳过返还但继续登记结论
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
    if success_sum >= order.total_cents:
        order.status = OrderStatus.REFUNDED.value
    else:
        # 理论不可达（部分退款已阻断）；历史遗留部分成功时交人工处理
        order.status = OrderStatus.REFUND_FAILED.value
        refund.note = (refund.note or "") + "；存在历史部分退款，需人工核对"


async def query_refund_status(
    db: AsyncSession, *, refund: MpRefund, queried_by_admin_id: int
) -> MpRefund:
    """按退款单号向微信查证真实状态并回写（超时/断连后的恢复路径）。

    微信状态映射：SUCCESS → success（联动订单/权益/积分，basis=wxpay_query）；
    CLOSED/ABNORMAL → failed（订单 refund_failed，可重新发起）；PROCESSING → 保持。
    """
    refund = (
        await db.execute(select(MpRefund).where(MpRefund.id == refund.id).with_for_update())
    ).scalar_one()
    if refund.channel != "wxpay":
        raise HTTPException(status_code=409, detail="人工退款单请用结论登记，无需查证")
    if refund.status not in (RefundStatus.PENDING.value, RefundStatus.PROCESSING.value):
        raise HTTPException(status_code=409, detail="该退款已有结论")
    order = (
        await db.execute(select(MpOrder).where(MpOrder.id == refund.order_id).with_for_update())
    ).scalar_one()
    cfg = wxpay_service.load_config()
    if cfg is None:
        raise HTTPException(status_code=503, detail="支付通道未配置，无法查证")
    try:
        data = await wxpay_service.query_refund(cfg, refund.refund_no)
    except wxpay_service.WxPayTimeout:
        raise HTTPException(status_code=504, detail="微信查证超时，请稍后重试")
    except wxpay_service.WxPayError as exc:
        raise HTTPException(status_code=502, detail=f"微信查证失败：{str(exc)[:80]}")

    wx_status = str(data.get("status") or "")
    if wx_status == "SUCCESS":
        refund.status = RefundStatus.SUCCESS.value
        refund.resolved_by_admin_id = queried_by_admin_id
        refund.finished_at = now_utc()
        refund.note = (refund.note or "") + "；结论来源：微信退款查单"
        await _apply_refund_success(
            db, refund=refund, order=order, basis="wxpay_query", resolved_by_admin_id=queried_by_admin_id
        )
    elif wx_status in ("CLOSED", "ABNORMAL"):
        refund.status = RefundStatus.FAILED.value
        refund.resolved_by_admin_id = queried_by_admin_id
        refund.finished_at = now_utc()
        refund.note = (refund.note or "") + f"；结论来源：微信退款查单（{wx_status}）"
        order.status = OrderStatus.REFUND_FAILED.value
    else:
        refund.note = (refund.note or "") + f"；微信状态：{wx_status or '未知'}（{now_utc():%Y-%m-%d %H:%M}Z 查证）"
    db.add(
        MpPaymentEvent(
            order_id=order.id,
            out_trade_no=order.order_no,
            event_type="query_sync",
            result=f"refund_query_{wx_status or 'unknown'}",
            detail={"refund_no": refund.refund_no},
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="退款查证结论登记冲突，请重试")
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
    """人工登记退款结论；success 联动订单 / 权益 / 积分返还，全部在同一事务。

    成功结论须有线下退款凭据（note 建议写凭据编号）；线上单的自动结论走
    query_refund_status（依据微信查单）。失败结论 → 订单 refund_failed 可重新发起。
    """
    refund = (
        await db.execute(select(MpRefund).where(MpRefund.id == refund.id).with_for_update())
    ).scalar_one()
    order = (
        await db.execute(select(MpOrder).where(MpOrder.id == refund.order_id).with_for_update())
    ).scalar_one()
    if refund.status not in (RefundStatus.PENDING.value, RefundStatus.PROCESSING.value):
        raise HTTPException(status_code=409, detail="该退款已有结论")
    if status == "success" and refund.channel == "wxpay":
        raise HTTPException(
            status_code=409,
            detail="线上退款单请先「查证微信状态」，以微信结果或核实凭据为准",
        )
    refund.status = RefundStatus.SUCCESS.value if status == "success" else RefundStatus.FAILED.value
    refund.note = note
    refund.resolved_by_admin_id = resolved_by_admin_id
    refund.finished_at = now_utc()

    if status == "success":
        await _apply_refund_success(
            db,
            refund=refund,
            order=order,
            basis="manual_resolve",
            resolved_by_admin_id=resolved_by_admin_id,
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
