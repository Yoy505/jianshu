"""小程序 · 服务包交易（工作一）：商品、订单、积分、权益、授权记录。

序列化只输出契约字段；跨用户资源一律 404。支付成功以服务端入账为准，
客户端 wx.requestPayment 的 success 回调只用于跳转结果页并轮询订单状态。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, pagination_params
from app.models import (
    MpEntitlement,
    MpOrder,
    MpPackage,
    MpPackageVersion,
    MpPointsAccount,
    MpPointsEntry,
    MpPurchaseBatch,
    MpOrg,
    MpRefund,
    MpUserConsent,
    OrderStatus,
    User,
)
from app.schemas.common import Page
from app.schemas.commerce import (
    CapabilitiesOut,
    ConsentOut,
    EntitlementOut,
    EntitlementPackageRef,
    OrderCreateIn,
    OrderDetailOut,
    OrderOut,
    PackageDetailOut,
    PackageRef,
    PackageSummaryOut,
    PointsAccountOut,
    PointsEntryOut,
    PrepayOut,
    Purchasable,
)
from app.services import commerce_service as svc
from app.services import wxpay_service

router = APIRouter()


# ───────────────────────── 序列化 ─────────────────────────


def _package_summary(
    package: MpPackage, version: MpPackageVersion, *, wxpay_enabled: bool, balance: int
) -> PackageSummaryOut:
    flags = svc.purchasable_flags(
        version,
        blockers=svc.version_blockers(version),
        wxpay_enabled=wxpay_enabled,
        balance=balance,
    )
    return PackageSummaryOut(
        id=package.id,
        name=package.name,
        subtitle=package.subtitle,
        cover_url=package.cover_url,
        price_cents=version.price_cents,
        points_cost=version.points_cost,
        validity_days=version.validity_days,
        benefit_count=len(version.benefits or []),
        purchasable=Purchasable(**flags),
    )


def _package_detail(package: MpPackage, version: MpPackageVersion, **kw) -> PackageDetailOut:
    base = _package_summary(package, version, **kw)
    return PackageDetailOut(
        **base.model_dump(),
        version_id=version.id,
        version_no=version.version_no,
        benefits=version.benefits or [],
        service_desc=version.service_desc,
        terms=version.terms,
        terms_version=f"v{version.version_no}",
    )


async def _refunds_of(db: AsyncSession, order_id: int) -> list[MpRefund]:
    return list(
        (
            await db.execute(
                select(MpRefund).where(MpRefund.order_id == order_id).order_by(MpRefund.id.desc())
            )
        ).scalars()
    )


async def _entitlement_id_of(db: AsyncSession, order_id: int) -> int | None:
    ent = (
        await db.execute(select(MpEntitlement.id).where(MpEntitlement.order_id == order_id))
    ).scalar_one_or_none()
    return ent


def _order_out(order: MpOrder, entitlement_id: int | None) -> OrderOut:
    return OrderOut(
        id=order.id,
        order_no=order.order_no,
        status=order.status,
        pay_type=order.pay_type,
        package=PackageRef(
            id=order.package_id,
            name=order.package_name,
            version_id=order.package_version_id,
            version_no=order.version_no,
        ),
        total_cents=order.total_cents,
        points_cost=order.points_cost,
        expires_at=order.expires_at,
        paid_at=order.paid_at,
        closed_at=order.closed_at,
        entitlement_id=entitlement_id,
        created_at=order.created_at,
    )


async def _entitlement_out(db: AsyncSession, ent: MpEntitlement) -> EntitlementOut:
    org_name = None
    if ent.batch_id is not None:
        org_name = (
            await db.execute(
                select(MpOrg.name).join(
                    MpPurchaseBatch, MpPurchaseBatch.org_id == MpOrg.id
                ).where(MpPurchaseBatch.id == ent.batch_id)
            )
        ).scalar_one_or_none()
    return EntitlementOut(
        id=ent.id,
        source_type=ent.source_type,
        status=ent.status,
        package=EntitlementPackageRef(
            id=ent.package_id, name=ent.package_name, version_no=ent.version_no
        ),
        benefits=ent.benefits or [],
        validity_days=ent.validity_days,
        activated_at=ent.activated_at,
        valid_until=ent.valid_until,
        org_name=org_name,
        order_id=ent.order_id,
        created_at=ent.created_at,
    )


async def _my_order(db: AsyncSession, user: User, order_id: int) -> MpOrder:
    order = await db.get(MpOrder, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="订单不存在")
    return order


async def _my_entitlement(db: AsyncSession, user: User, ent_id: int) -> MpEntitlement:
    ent = await db.get(MpEntitlement, ent_id)
    if ent is None or not svc.entitlement_visible_to(user, ent):
        raise HTTPException(status_code=404, detail="权益不存在")
    return ent


# ───────────────────────── 商品 ─────────────────────────


@router.get("/commerce/capabilities", response_model=CapabilitiesOut)
async def commerce_capabilities(user: User = Depends(get_current_user)) -> CapabilitiesOut:
    enabled = wxpay_service.wxpay_enabled()
    return CapabilitiesOut(
        wxpay_enabled=enabled,
        points_enabled=True,
        wxpay_reason=None if enabled else "支付通道未开通",
    )


@router.get("/packages", response_model=Page[PackageSummaryOut])
async def list_packages(
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> Page[PackageSummaryOut]:
    page, page_size = pagination
    # 只返回上架且指向有效版本的服务包；余额不影响列表，只在详情参与可购判定
    total = (
        await db.execute(
            select(func.count())
            .select_from(MpPackage)
            .join(MpPackageVersion, MpPackage.current_version_id == MpPackageVersion.id)
            .where(MpPackage.status == "on_shelf")
        )
    ).scalar()
    rows = (
        await db.execute(
            select(MpPackage, MpPackageVersion)
            .join(MpPackageVersion, MpPackage.current_version_id == MpPackageVersion.id)
            .where(MpPackage.status == "on_shelf")
            .order_by(MpPackage.sort.asc(), MpPackage.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    enabled = wxpay_service.wxpay_enabled()
    return Page(
        items=[_package_summary(p, v, wxpay_enabled=enabled, balance=0) for p, v in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/packages/{package_id}", response_model=PackageDetailOut)
async def package_detail(
    package_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PackageDetailOut:
    package, version = await svc.get_package_with_version(db, package_id)
    if package.status != "on_shelf" or version is None:
        raise HTTPException(status_code=404, detail="服务包不存在或已下架")
    balance = await svc.get_points_balance(db, user.id)
    return _package_detail(
        package, version, wxpay_enabled=wxpay_service.wxpay_enabled(), balance=balance
    )


# ───────────────────────── 订单 ─────────────────────────


@router.post("/orders", response_model=OrderOut)
async def create_order(
    body: OrderCreateIn,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order, created = await svc.create_order(
        db,
        user,
        client_order_id=body.client_order_id,
        package_id=body.package_id,
        pay_type=body.pay_type,
        agree_terms=body.agree_terms,
    )
    response.status_code = 201 if created else 200
    return _order_out(order, await _entitlement_id_of(db, order.id))


@router.get("/orders", response_model=Page[OrderOut])
async def list_orders(
    status: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[OrderOut]:
    page, page_size = pagination
    filters = [MpOrder.user_id == user.id]
    if status:
        filters.append(MpOrder.status == status)
    total = (
        await db.execute(select(func.count()).select_from(MpOrder).where(*filters))
    ).scalar()
    orders = (
        await db.execute(
            select(MpOrder)
            .where(*filters)
            .order_by(MpOrder.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    for order in orders:
        await svc._lazy_close(db, order)
    items = [_order_out(o, await _entitlement_id_of(db, o.id)) for o in orders]
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.get("/orders/{order_id}", response_model=OrderDetailOut)
async def order_detail(
    order_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderDetailOut:
    order = await _my_order(db, user, order_id)
    await svc._lazy_close(db, order)
    base = _order_out(order, await _entitlement_id_of(db, order.id))
    refunds = await _refunds_of(db, order.id)
    return OrderDetailOut(
        **base.model_dump(),
        refunds=[
            {
                "id": r.id,
                "refund_no": r.refund_no,
                "channel": r.channel,
                "status": r.status,
                "amount_cents": r.amount_cents,
                "reason": r.reason,
                "note": r.note,
                "created_at": r.created_at,
                "finished_at": r.finished_at,
            }
            for r in refunds
        ],
    )


@router.post("/orders/{order_id}/prepay", response_model=PrepayOut)
async def order_prepay(
    order_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PrepayOut:
    order = await _my_order(db, user, order_id)
    payment = await svc.create_prepay(db, user, order)
    if not payment.prepay_id:
        raise HTTPException(status_code=502, detail="微信支付下单失败，请稍后重试")
    cfg = wxpay_service.load_config()
    if cfg is None:  # 理论不可达：create_prepay 已校验
        raise HTTPException(status_code=503, detail="支付通道未开通")
    return PrepayOut(**wxpay_service.request_payment_params(cfg, payment.prepay_id))


@router.post("/orders/{order_id}/close", response_model=OrderOut)
async def close_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await _my_order(db, user, order_id)
    if order.status != OrderStatus.PENDING_PAYMENT.value:
        raise HTTPException(status_code=409, detail="订单当前状态不可取消")
    await svc.close_order_by_user(db, order, reason="user")
    return _order_out(order, await _entitlement_id_of(db, order.id))


# ───────────────────────── 积分 ─────────────────────────


@router.get("/points", response_model=PointsAccountOut)
async def my_points(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> PointsAccountOut:
    account = await db.get(MpPointsAccount, user.id)
    return PointsAccountOut(balance=int(account.balance) if account else 0, updated_at=account.updated_at if account else None)


@router.get("/points/entries", response_model=Page[PointsEntryOut])
async def my_points_entries(
    pagination: tuple[int, int] = Depends(pagination_params),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[PointsEntryOut]:
    page, page_size = pagination
    total = (
        await db.execute(
            select(func.count()).select_from(MpPointsEntry).where(MpPointsEntry.user_id == user.id)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(MpPointsEntry)
            .where(MpPointsEntry.user_id == user.id)
            .order_by(MpPointsEntry.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[
            PointsEntryOut(
                id=e.id,
                delta=e.delta,
                balance_after=e.balance_after,
                biz_type=e.biz_type,
                order_id=e.order_id,
                note=e.note,
                created_at=e.created_at,
            )
            for e in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


# ───────────────────────── 权益 ─────────────────────────


@router.get("/entitlements", response_model=Page[EntitlementOut])
async def my_entitlements(
    status: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[EntitlementOut]:
    page, page_size = pagination
    visible = [MpEntitlement.user_id == user.id]
    if user.phone:
        visible.append(
            (MpEntitlement.phone == user.phone)
            & MpEntitlement.status.in_(["pending", "rejected"])
        )
    filters = [or_(*visible)]
    if status:
        filters.append(MpEntitlement.status == status)
    total = (
        await db.execute(
            select(func.count()).select_from(MpEntitlement).where(*filters)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(MpEntitlement)
            .where(*filters)
            .order_by(MpEntitlement.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[await _entitlement_out(db, e) for e in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/entitlements/{ent_id}", response_model=EntitlementOut)
async def entitlement_detail(
    ent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntitlementOut:
    ent = await _my_entitlement(db, user, ent_id)
    return await _entitlement_out(db, ent)


@router.post("/entitlements/{ent_id}/claim", response_model=EntitlementOut)
async def claim(
    ent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntitlementOut:
    ent = await _my_entitlement(db, user, ent_id)
    ent = await svc.claim_entitlement(db, user, ent)
    return await _entitlement_out(db, ent)


@router.post("/entitlements/{ent_id}/reject", response_model=EntitlementOut)
async def reject(
    ent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EntitlementOut:
    ent = await _my_entitlement(db, user, ent_id)
    ent = await svc.reject_entitlement(db, user, ent)
    return await _entitlement_out(db, ent)


# ───────────────────────── 授权记录 ─────────────────────────


@router.get("/consents", response_model=Page[ConsentOut])
async def my_consents(
    purpose: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[ConsentOut]:
    page, page_size = pagination
    filters = [MpUserConsent.user_id == user.id]
    if purpose:
        filters.append(MpUserConsent.purpose == purpose)
    total = (
        await db.execute(
            select(func.count()).select_from(MpUserConsent).where(*filters)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(MpUserConsent)
            .where(*filters)
            .order_by(MpUserConsent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[
            ConsentOut(
                id=c.id,
                purpose=c.purpose,
                version=c.version,
                text=c.text,
                granted_at=c.granted_at,
                revoked_at=c.revoked_at,
            )
            for c in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.post("/consents/{consent_id}/revoke", response_model=ConsentOut)
async def revoke_consent(
    consent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConsentOut:
    consent = await db.get(MpUserConsent, consent_id)
    if consent is None or consent.user_id != user.id:
        raise HTTPException(status_code=404, detail="授权记录不存在")
    consent = await svc.revoke_consent(db, user, consent)
    # 工作二最小联动（S1）：履约用途撤回 → 实例时间线 + 审计 + 门禁回退；
    # 非履约用途不受影响。撤回授权不触发退款。
    if consent.purpose.startswith("steward_"):
        from app.services import steward_service as steward_svc

        await steward_svc.on_consent_revoked(db, consent)
    return ConsentOut(
        id=consent.id,
        purpose=consent.purpose,
        version=consent.version,
        text=consent.text,
        granted_at=consent.granted_at,
        revoked_at=consent.revoked_at,
    )
