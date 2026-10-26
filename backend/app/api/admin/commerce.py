"""后台 · 服务包交易运营（工作一）：商品/版本/上下架、订单/对账/退款、积分调整、
单位与批次（预览→确认）、权益异常处理。

审计模式：先 db.add(audit(...)) 再调用服务层（服务层 commit 时审计与业务同事务生效，
业务失败回滚则审计一并丢弃）。写操作均需对应 commerce.* 权限点（docs/api.md 矩阵）。
"""

import hashlib
import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db, pagination_params
from app.core.permissions import (
    COMMERCE_BATCH_CONFIRM,
    COMMERCE_BATCH_MANAGE,
    COMMERCE_ORDER_READ,
    COMMERCE_ORDER_REFUND,
    COMMERCE_POINTS_ADJUST,
    COMMERCE_PRODUCT_MANAGE,
    COMMERCE_SALE_RELEASE,
    require_permission,
)
from app.core.security import hash_password
from app.core.storage_paths import configured_storage_path
from app.models import (
    AdminUser,
    EntitlementSource,
    EntitlementStatus,
    MpEntitlementEvent,
    MpSaleRelease,
    MpBatchRow,
    MpEntitlement,
    MpOrder,
    MpOrg,
    MpOrgAccount,
    MpPackage,
    MpPackageVersion,
    MpPayment,
    MpPaymentEvent,
    MpPointsEntry,
    MpPurchaseBatch,
    MpRefund,
    User,
)
from app.schemas.common import Page, mask_phone
from app.schemas.commerce import (
    AdminEntitlementOut,
    AdminOrderDetailOut,
    AdminOrderOut,
    AdminPackageDetailOut,
    AdminPackageOut,
    AdminRefundOut,
    BatchDetailOut,
    BatchOut,
    BatchPackageRef,
    BatchPreviewIn,
    BatchPreviewOut,
    BatchRowOut,
    BatchSummary,
    EntitlementPackageRef,
    EntitlementReassignIn,
    EntitlementVoidIn,
    OrgAccountCreateIn,
    OrgAccountOut,
    OrgBrief,
    OrgCreateIn,
    OrgOut,
    PackageCreateIn,
    PackagePatchIn,
    PaymentEventOut,
    PaymentOut,
    PointsAdjustIn,
    ReleaseCreateIn,
    ReleaseOut,
    ReleaseRevokeIn,
    RefundCreateIn,
    RefundOut,
    RefundResolveIn,
    ShelfIn,
    UserBrief,
    VersionCreateIn,
    VersionOut,
)
from app.services import commerce_service as svc
from app.services import wxpay_service
from app.services.audit_service import audit

router = APIRouter(prefix="/admin/mp", tags=["admin-commerce"])


# ───────────────────────── 序列化 ─────────────────────────


async def _version_out(db: AsyncSession, version: MpPackageVersion) -> VersionOut:
    creator = (
        await db.execute(select(AdminUser.name).where(AdminUser.id == version.created_by_admin_id))
    ).scalar_one_or_none()
    return VersionOut(
        id=version.id,
        version_no=version.version_no,
        price_cents=version.price_cents,
        points_cost=version.points_cost,
        validity_days=version.validity_days,
        benefits=version.benefits or [],
        service_desc=version.service_desc,
        terms=version.terms,
        created_by=creator,
        created_at=version.created_at,
    )


async def _release_out(db: AsyncSession, release: MpSaleRelease) -> ReleaseOut:
    released_by = (
        await db.execute(
            select(AdminUser.name).where(AdminUser.id == release.released_by_admin_id)
        )
    ).scalar_one_or_none()
    revoked_by = (
        await db.execute(
            select(AdminUser.name).where(AdminUser.id == release.revoked_by_admin_id)
        )
    ).scalar_one_or_none()
    version = await db.get(MpPackageVersion, release.version_id)
    return ReleaseOut(
        id=release.id,
        package_id=release.package_id,
        version_id=release.version_id,
        version_no=version.version_no if version else 0,
        env=release.env,
        basis=release.basis,
        released_by=released_by,
        released_at=release.released_at,
        revoked_at=release.revoked_at,
        revoked_by=revoked_by,
        revoke_reason=release.revoke_reason,
    )


async def _package_out(
    db: AsyncSession, package: MpPackage, *, with_versions: bool = False
) -> AdminPackageOut | AdminPackageDetailOut:
    version = (
        await db.get(MpPackageVersion, package.current_version_id)
        if package.current_version_id
        else None
    )
    base = AdminPackageOut(
        id=package.id,
        name=package.name,
        subtitle=package.subtitle,
        cover_url=package.cover_url,
        sort=package.sort,
        status=package.status,
        current_version=await _version_out(db, version) if version else None,
        blockers=svc.version_blockers(version),
        created_at=package.created_at,
        updated_at=package.updated_at,
    )
    if not with_versions:
        return base
    versions = (
        await db.execute(
            select(MpPackageVersion)
            .where(MpPackageVersion.package_id == package.id)
            .order_by(MpPackageVersion.version_no.desc())
        )
    ).scalars().all()
    releases = (
        await db.execute(
            select(MpSaleRelease)
            .where(MpSaleRelease.package_id == package.id)
            .order_by(MpSaleRelease.id.desc())
            .limit(50)
        )
    ).scalars().all()
    return AdminPackageDetailOut(
        **base.model_dump(),
        versions=[await _version_out(db, v) for v in versions],
        releases=[await _release_out(db, r) for r in releases],
    )


async def _user_brief(db: AsyncSession, user_id: int) -> UserBrief:
    user = await db.get(User, user_id)
    return UserBrief(id=user.id, nickname=user.nickname, phone=mask_phone(user.phone))


async def _refund_out(db: AsyncSession, refund: MpRefund, *, admin_view: bool = False):
    requested_by = (
        await db.execute(
            select(AdminUser.name).where(AdminUser.id == refund.requested_by_admin_id)
        )
    ).scalar_one_or_none()
    resolved_by = (
        await db.execute(
            select(AdminUser.name).where(AdminUser.id == refund.resolved_by_admin_id)
        )
    ).scalar_one_or_none()
    common = {
        "id": refund.id,
        "refund_no": refund.refund_no,
        "channel": refund.channel,
        "status": refund.status,
        "amount_cents": refund.amount_cents,
        "reason": refund.reason,
        "note": refund.note,
        "created_at": refund.created_at,
        "finished_at": refund.finished_at,
    }
    if not admin_view:
        return RefundOut(**common)
    order = await db.get(MpOrder, refund.order_id)
    return AdminRefundOut(
        **common,
        order_id=refund.order_id,
        order_no=order.order_no,
        pay_type=order.pay_type,
        user=await _user_brief(db, order.user_id),
        requested_by=requested_by,
        resolved_by=resolved_by,
    )


def _entitlement_core(ent: MpEntitlement) -> dict:
    return {
        "id": ent.id,
        "source_type": ent.source_type,
        "status": ent.status,
        "package": EntitlementPackageRef(
            id=ent.package_id, name=ent.package_name, version_no=ent.version_no
        ),
        "benefits": ent.benefits or [],
        "validity_days": ent.validity_days,
        "activated_at": ent.activated_at,
        "valid_until": ent.valid_until,
        "org_name": None,
        "order_id": ent.order_id,
        "created_at": ent.created_at,
    }


async def _admin_entitlement_out(db: AsyncSession, ent: MpEntitlement) -> AdminEntitlementOut:
    user = await _user_brief(db, ent.user_id) if ent.user_id else None
    return AdminEntitlementOut(
        **_entitlement_core(ent),
        phone=mask_phone(ent.phone),
        batch_id=ent.batch_id,
        user=user,
        void_reason=ent.void_reason,
    )


def _order_core(order: MpOrder, entitlement_id: int | None) -> dict:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "pay_type": order.pay_type,
        "package": {
            "id": order.package_id,
            "name": order.package_name,
            "version_id": order.package_version_id,
            "version_no": order.version_no,
        },
        "total_cents": order.total_cents,
        "points_cost": order.points_cost,
        "expires_at": order.expires_at,
        "paid_at": order.paid_at,
        "closed_at": order.closed_at,
        "entitlement_id": entitlement_id,
        "created_at": order.created_at,
    }


def _batch_rows_out(rows: list[MpBatchRow], ent_status: dict[int, str] | None = None) -> list[BatchRowOut]:
    return [
        BatchRowOut(
            row_no=r.row_no,
            phone=mask_phone(r.phone) or "",
            status=r.status,
            error_detail=r.error_detail,
            entitlement_status=(ent_status or {}).get(r.id),
        )
        for r in rows
    ]


async def _batch_out(
    db: AsyncSession, batch: MpPurchaseBatch, *, rows: list[MpBatchRow], with_rows: bool = False
):
    org = await db.get(MpOrg, batch.org_id)
    version = await db.get(MpPackageVersion, batch.package_version_id)
    package = await db.get(MpPackage, batch.package_id)
    uploaded_by = (
        await db.execute(
            select(AdminUser.name).where(AdminUser.id == batch.uploaded_by_admin_id)
        )
    ).scalar_one_or_none()
    confirmed_by = (
        await db.execute(
            select(AdminUser.name).where(AdminUser.id == batch.confirmed_by_admin_id)
        )
    ).scalar_one_or_none()
    counts = await svc.batch_status_counts(db, batch.id)
    summary = BatchSummary(
        total=len(rows),
        valid=sum(1 for r in rows if r.status == "valid"),
        duplicate=sum(1 for r in rows if r.status == "duplicate"),
        format_error=sum(1 for r in rows if r.status == "format_error"),
        over_limit=sum(1 for r in rows if r.status == "over_limit"),
    )
    common = {
        "id": batch.id,
        "batch_no": batch.batch_no,
        "org": OrgBrief(id=org.id, name=org.name),
        "package": BatchPackageRef(id=package.id, name=package.name, version_no=version.version_no),
        "quantity": batch.quantity,
        "file_name": batch.file_name,
        "file_sha256": batch.file_sha256,
        "status": batch.status,
        "summary": summary,
        "issued_count": counts["issued"],
        "claimed_count": counts["claimed"],
        "uploaded_by": uploaded_by,
        "confirmed_by": confirmed_by,
        "confirmed_at": batch.confirmed_at,
        "created_at": batch.created_at,
    }
    if not with_rows:
        return BatchOut(**common)
    return BatchDetailOut(**common, rows=_batch_rows_out(rows))


# ───────────────────────── 商品与版本 ─────────────────────────


@router.get("/packages", response_model=Page[AdminPackageOut])
async def admin_list_packages(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> Page[AdminPackageOut]:
    page, page_size = pagination
    filters = []
    if status:
        filters.append(MpPackage.status == status)
    if q:
        filters.append(MpPackage.name.like(f"%{q}%"))
    total = (
        await db.execute(select(func.count()).select_from(MpPackage).where(*filters))
    ).scalar()
    packages = (
        await db.execute(
            select(MpPackage)
            .where(*filters)
            .order_by(MpPackage.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[await _package_out(db, p) for p in packages],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.post("/packages", response_model=AdminPackageOut, status_code=201)
async def admin_create_package(
    body: PackageCreateIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_PRODUCT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AdminPackageOut:
    package = MpPackage(
        name=body.name, subtitle=body.subtitle, cover_url=body.cover_url, sort=body.sort
    )
    db.add(package)
    await audit(
        db,
        action="commerce.package.create",
        resource_type="package",
        detail={"name": body.name},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(package)
    return await _package_out(db, package)


@router.get("/packages/{package_id}", response_model=AdminPackageDetailOut)
async def admin_package_detail(
    package_id: int,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPackageDetailOut:
    package = await db.get(MpPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="服务包不存在")
    return await _package_out(db, package, with_versions=True)


@router.patch("/packages/{package_id}", response_model=AdminPackageOut)
async def admin_patch_package(
    package_id: int,
    body: PackagePatchIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_PRODUCT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AdminPackageOut:
    package = await db.get(MpPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="服务包不存在")
    for field in ("name", "subtitle", "cover_url", "sort"):
        value = getattr(body, field)
        if value is not None:
            setattr(package, field, value)
    await audit(
        db,
        action="commerce.package.update",
        resource_type="package",
        resource_id=package.id,
        detail=body.model_dump(exclude_none=True),
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(package)
    return await _package_out(db, package)


@router.post("/packages/{package_id}/versions", response_model=VersionOut, status_code=201)
async def admin_create_version(
    package_id: int,
    body: VersionCreateIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_PRODUCT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> VersionOut:
    package = await db.get(MpPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="服务包不存在")
    max_no = (
        await db.execute(
            select(func.max(MpPackageVersion.version_no)).where(
                MpPackageVersion.package_id == package.id
            )
        )
    ).scalar()
    version = MpPackageVersion(
        package_id=package.id,
        version_no=int(max_no or 0) + 1,
        price_cents=body.price_cents,
        points_cost=body.points_cost,
        validity_days=body.validity_days,
        benefits=[b.model_dump() for b in body.benefits],
        service_desc=body.service_desc,
        terms=body.terms,
        created_by_admin_id=admin.id,
    )
    db.add(version)
    await audit(
        db,
        action="commerce.package.version_create",
        resource_type="package",
        resource_id=package.id,
        detail={"version_no": version.version_no, "price_cents": body.price_cents},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(version)
    return await _version_out(db, version)


@router.post("/packages/{package_id}/shelf", response_model=AdminPackageOut)
async def admin_shelf_package(
    package_id: int,
    body: ShelfIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_PRODUCT_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AdminPackageOut:
    package = (
        await db.execute(select(MpPackage).where(MpPackage.id == package_id).with_for_update())
    ).scalar_one_or_none()
    if package is None:
        raise HTTPException(status_code=404, detail="服务包不存在")
    if body.on_shelf:
        if body.version_id is None:
            raise HTTPException(status_code=422, detail="上架必须指定版本")
        version = await db.get(MpPackageVersion, body.version_id)
        if version is None or version.package_id != package.id:
            raise HTTPException(status_code=404, detail="版本不存在")
        blockers = svc.version_blockers(version)
        if blockers:
            return JSONResponse(
                status_code=409,
                content={"detail": "暂不可上架：" + "、".join(blockers), "blockers": blockers},
            )
        package.current_version_id = version.id
        package.status = "on_shelf"
        action_detail = {"on_shelf": True, "version_id": version.id}
    else:
        package.status = "off_shelf"
        action_detail = {"on_shelf": False}
    await audit(
        db,
        action="commerce.package.shelf",
        resource_type="package",
        resource_id=package.id,
        detail=action_detail,
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(package)
    return await _package_out(db, package)


# ───────────────────────── 开售放行（R8，仅管理员） ─────────────────────────


@router.post("/packages/{package_id}/releases", response_model=ReleaseOut, status_code=201)
async def admin_create_release(
    package_id: int,
    body: ReleaseCreateIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_SALE_RELEASE)),
    db: AsyncSession = Depends(get_db),
) -> ReleaseOut:
    package = await db.get(MpPackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="服务包不存在")
    version = await db.get(MpPackageVersion, body.version_id)
    if version is None or version.package_id != package.id:
        raise HTTPException(status_code=404, detail="版本不存在")
    # 同包同版本同环境已有未撤销放行 → 409（先撤销才能重放）
    active = (
        await db.execute(
            select(MpSaleRelease)
            .where(
                MpSaleRelease.package_id == package.id,
                MpSaleRelease.version_id == version.id,
                MpSaleRelease.env == body.env,
                MpSaleRelease.revoked_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None:
        raise HTTPException(status_code=409, detail="该版本在该环境已有生效中的放行记录")
    release = MpSaleRelease(
        package_id=package.id,
        version_id=version.id,
        env=body.env,
        basis=body.basis,
        released_by_admin_id=admin.id,
    )
    db.add(release)
    await audit(
        db,
        action="commerce.package.release",
        resource_type="package",
        resource_id=package.id,
        detail={"version_id": version.id, "env": body.env, "basis": body.basis},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(release)
    return await _release_out(db, release)


@router.post("/releases/{release_id}/revoke", response_model=ReleaseOut)
async def admin_revoke_release(
    release_id: int,
    body: ReleaseRevokeIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_SALE_RELEASE)),
    db: AsyncSession = Depends(get_db),
) -> ReleaseOut:
    from sqlalchemy import update

    release = await db.get(MpSaleRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="放行记录不存在")
    if release.revoked_at is not None:
        raise HTTPException(status_code=409, detail="该放行记录已撤销")
    result = await db.execute(
        update(MpSaleRelease)
        .where(MpSaleRelease.id == release.id, MpSaleRelease.revoked_at.is_(None))
        .values(
            revoked_at=func.now(),
            revoked_by_admin_id=admin.id,
            revoke_reason=body.reason,
        )
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该放行记录已撤销")
    await audit(
        db,
        action="commerce.package.release_revoke",
        resource_type="package",
        resource_id=release.package_id,
        detail={"release_id": release.id, "reason": body.reason},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(release)
    return await _release_out(db, release)


# ───────────────────────── 订单 / 对账 / 退款 ─────────────────────────


async def _admin_order(db: AsyncSession, order_id: int) -> MpOrder:
    order = await db.get(MpOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    await svc._lazy_close(db, order)
    return order


@router.get("/orders", response_model=Page[AdminOrderOut])
async def admin_list_orders(
    status: str | None = Query(default=None),
    pay_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin: AdminUser = Depends(require_permission(COMMERCE_ORDER_READ)),
    db: AsyncSession = Depends(get_db),
) -> Page[AdminOrderOut]:
    page, page_size = pagination
    filters = []
    if status:
        filters.append(MpOrder.status == status)
    if pay_type:
        filters.append(MpOrder.pay_type == pay_type)
    if q:
        like = f"%{q}%"
        filters.append(
            or_(
                MpOrder.order_no.like(like),
                User.nickname.like(like),
                User.phone.like(like),
            )
        )
    count_q = select(func.count()).select_from(MpOrder)
    if filters:
        count_q = count_q.join(User, MpOrder.user_id == User.id).where(*filters)
    total = (await db.execute(count_q)).scalar()
    list_q = select(MpOrder).join(User, MpOrder.user_id == User.id)
    if filters:
        list_q = list_q.where(*filters)
    orders = (
        await db.execute(
            list_q.order_by(MpOrder.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    items = []
    for order in orders:
        ent_id = (
            await db.execute(
                select(MpEntitlement.id).where(MpEntitlement.order_id == order.id)
            )
        ).scalar_one_or_none()
        items.append(
            AdminOrderOut(**_order_core(order, ent_id), user=await _user_brief(db, order.user_id))
        )
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.get("/orders/{order_id}", response_model=AdminOrderDetailOut)
async def admin_order_detail(
    order_id: int,
    admin: AdminUser = Depends(require_permission(COMMERCE_ORDER_READ)),
    db: AsyncSession = Depends(get_db),
) -> AdminOrderDetailOut:
    order = await _admin_order(db, order_id)
    payments = (
        await db.execute(
            select(MpPayment).where(MpPayment.order_id == order.id).order_by(MpPayment.id.desc())
        )
    ).scalars().all()
    events = (
        await db.execute(
            select(MpPaymentEvent)
            .where(MpPaymentEvent.order_id == order.id)
            .order_by(MpPaymentEvent.id.desc())
            .limit(50)
        )
    ).scalars().all()
    refunds = (
        await db.execute(
            select(MpRefund).where(MpRefund.order_id == order.id).order_by(MpRefund.id.desc())
        )
    ).scalars().all()
    ent = (
        await db.execute(select(MpEntitlement).where(MpEntitlement.order_id == order.id))
    ).scalar_one_or_none()
    return AdminOrderDetailOut(
        **_order_core(order, ent.id if ent else None),
        user=await _user_brief(db, order.user_id),
        payments=[
            PaymentOut(
                id=p.id,
                prepay_id=p.prepay_id,
                transaction_id=p.transaction_id,
                status=p.status,
                amount_cents=p.amount_cents,
                fail_reason=p.fail_reason,
                created_at=p.created_at,
            )
            for p in payments
        ],
        events=[
            PaymentEventOut(
                id=e.id,
                event_type=e.event_type,
                result=e.result,
                detail=e.detail,
                created_at=e.created_at,
            )
            for e in events
        ],
        refunds=[await _refund_out(db, r) for r in refunds],
        entitlement=await _admin_entitlement_out(db, ent) if ent else None,
    )


@router.post("/orders/{order_id}/sync", response_model=AdminOrderDetailOut)
async def admin_sync_order(
    order_id: int,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_ORDER_REFUND)),
    db: AsyncSession = Depends(get_db),
) -> AdminOrderDetailOut:
    order = await _admin_order(db, order_id)
    cfg = wxpay_service.load_config()
    if cfg is None:
        raise HTTPException(status_code=503, detail="支付通道未开通，无法对账")
    try:
        data = await wxpay_service.query_order(cfg, order.order_no)
    except wxpay_service.WxPayError as exc:
        raise HTTPException(status_code=502, detail=f"微信查单失败：{str(exc)[:80]}")
    result = await svc.process_payment_result(
        db,
        out_trade_no=order.order_no,
        transaction_id=data.get("transaction_id"),
        trade_state=str(data.get("trade_state") or ""),
        amount_total=(data.get("amount") or {}).get("total"),
        event_type="query_sync",
        currency=(data.get("amount") or {}).get("currency"),
    )
    if result in ("invalid_success_fields", "invalid_state", "transaction_reused"):
        raise HTTPException(status_code=422, detail="微信返回的成功结果字段不完整或不匹配，未入账")
    if result == "amount_mismatch":
        raise HTTPException(status_code=409, detail="微信金额与订单不一致，未入账")
    await audit(
        db,
        action="commerce.order.sync",
        resource_type="order",
        resource_id=order.id,
        detail={"result": result},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    return await admin_order_detail(order_id, admin=admin, db=db)


@router.post("/orders/{order_id}/refund", response_model=AdminRefundOut)
async def admin_request_refund(
    order_id: int,
    body: RefundCreateIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_ORDER_REFUND)),
    db: AsyncSession = Depends(get_db),
) -> AdminRefundOut:
    order = await _admin_order(db, order_id)
    await audit(
        db,
        action="commerce.order.refund",
        resource_type="order",
        resource_id=order.id,
        detail={
            "channel": body.channel,
            "amount_cents": body.amount_cents or order.total_cents,
            "reason": body.reason,
        },
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    refund = await svc.request_refund(
        db,
        order=order,
        channel=body.channel,
        amount_cents=body.amount_cents,
        reason=body.reason,
        requested_by_admin_id=admin.id,
    )
    return await _refund_out(db, refund, admin_view=True)


@router.get("/refunds", response_model=Page[AdminRefundOut])
async def admin_list_refunds(
    status: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin: AdminUser = Depends(require_permission(COMMERCE_ORDER_READ)),
    db: AsyncSession = Depends(get_db),
) -> Page[AdminRefundOut]:
    page, page_size = pagination
    filters = [MpRefund.status == status] if status else [
        MpRefund.status.in_(["pending", "processing", "failed"])
    ]
    total = (
        await db.execute(select(func.count()).select_from(MpRefund).where(*filters))
    ).scalar()
    refunds = (
        await db.execute(
            select(MpRefund)
            .where(*filters)
            .order_by(MpRefund.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[await _refund_out(db, r, admin_view=True) for r in refunds],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.post("/refunds/{refund_id}/query", response_model=AdminRefundOut)
async def admin_query_refund(
    refund_id: int,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_ORDER_REFUND)),
    db: AsyncSession = Depends(get_db),
) -> AdminRefundOut:
    """按退款单号向微信查证真实状态并回写结论（超时/结果未知的恢复路径）。"""
    refund = await db.get(MpRefund, refund_id)
    if refund is None:
        raise HTTPException(status_code=404, detail="退款记录不存在")
    await audit(
        db,
        action="commerce.refund.query",
        resource_type="refund",
        resource_id=refund.id,
        detail={"refund_no": refund.refund_no},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    refund = await svc.query_refund_status(db, refund=refund, queried_by_admin_id=admin.id)
    return await _refund_out(db, refund, admin_view=True)


@router.post("/refunds/{refund_id}/resolve", response_model=AdminRefundOut)
async def admin_resolve_refund(
    refund_id: int,
    body: RefundResolveIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_ORDER_REFUND)),
    db: AsyncSession = Depends(get_db),
) -> AdminRefundOut:
    refund = await db.get(MpRefund, refund_id)
    if refund is None:
        raise HTTPException(status_code=404, detail="退款记录不存在")
    await audit(
        db,
        action="commerce.refund.resolve",
        resource_type="refund",
        resource_id=refund.id,
        detail={"status": body.status, "note": body.note},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    refund = await svc.resolve_refund(
        db, refund=refund, status=body.status, note=body.note, resolved_by_admin_id=admin.id
    )
    return await _refund_out(db, refund, admin_view=True)


# ───────────────────────── 积分 ─────────────────────────


@router.post("/points/adjust")
async def admin_adjust_points(
    body: PointsAdjustIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_POINTS_ADJUST)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await audit(
        db,
        action="commerce.points.adjust",
        resource_type="user",
        resource_id=body.user_id,
        detail={"delta": body.delta, "note": body.note},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    entry = await svc.apply_points(
        db,
        user_id=body.user_id,
        delta=body.delta,
        biz_type="admin_adjust",
        note=body.note,
        admin_id=admin.id,
    )
    await db.commit()
    return {"entry_id": entry.id, "balance_after": entry.balance_after}


# ───────────────────────── 采购单位 ─────────────────────────


@router.get("/orgs", response_model=Page[OrgOut])
async def admin_list_orgs(
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> Page[OrgOut]:
    page, page_size = pagination
    filters = [MpOrg.name.like(f"%{q}%")] if q else []
    total = (await db.execute(select(func.count()).select_from(MpOrg).where(*filters))).scalar()
    orgs = (
        await db.execute(
            select(MpOrg).where(*filters).order_by(MpOrg.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    items = []
    for org in orgs:
        account_count = (
            await db.execute(
                select(func.count()).select_from(MpOrgAccount).where(MpOrgAccount.org_id == org.id)
            )
        ).scalar()
        items.append(
            OrgOut(
                id=org.id,
                name=org.name,
                contact_name=org.contact_name,
                contact_phone=mask_phone(org.contact_phone),
                note=org.note,
                status=org.status,
                account_count=int(account_count or 0),
                created_at=org.created_at,
            )
        )
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.post("/orgs", response_model=OrgOut, status_code=201)
async def admin_create_org(
    body: OrgCreateIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> OrgOut:
    org = MpOrg(
        name=body.name,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
        note=body.note,
    )
    db.add(org)
    await audit(
        db,
        action="commerce.org.create",
        resource_type="org",
        detail={"name": body.name},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="单位名称已存在")
    await db.refresh(org)
    return OrgOut(
        id=org.id,
        name=org.name,
        contact_name=org.contact_name,
        contact_phone=mask_phone(org.contact_phone),
        note=org.note,
        status=org.status,
        account_count=0,
        created_at=org.created_at,
    )


@router.post("/orgs/{org_id}/accounts", response_model=OrgAccountOut, status_code=201)
async def admin_create_org_account(
    org_id: int,
    body: OrgAccountCreateIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> OrgAccountOut:
    org = await db.get(MpOrg, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="单位不存在")
    account = MpOrgAccount(
        org_id=org.id, username=body.username, password_hash=hash_password(body.password)
    )
    db.add(account)
    await audit(
        db,
        action="commerce.org.account_create",
        resource_type="org",
        resource_id=org.id,
        detail={"username": body.username},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在")
    await db.refresh(account)
    return OrgAccountOut(
        id=account.id, org_id=account.org_id, username=account.username, status=account.status, created_at=account.created_at
    )


# ───────────────────────── 批次 ─────────────────────────


@router.post("/batches/preview", response_model=BatchPreviewOut, status_code=201)
async def admin_preview_batch(
    body: BatchPreviewIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> BatchPreviewOut:
    await audit(
        db,
        action="commerce.batch.preview",
        resource_type="batch",
        detail={
            "org_id": body.org_id,
            "package_id": body.package_id,
            "version_id": body.version_id,
            "quantity": body.quantity,
            "file_name": body.file_name,
            "row_count": len(body.rows),
        },
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    batch, rows, summary = await svc.preview_batch(
        db,
        uploaded_by_id=admin.id,
        org_id=body.org_id,
        package_id=body.package_id,
        version_id=body.version_id,
        quantity=body.quantity,
        file_name=body.file_name,
        raw_rows=body.rows,
    )
    batch_out = await _batch_out(db, batch, rows=rows)
    return BatchPreviewOut(batch=batch_out, rows=_batch_rows_out(rows), summary=summary)


@router.post("/batches/preview-file", response_model=BatchPreviewOut, status_code=201)
async def admin_preview_batch_file(
    request: Request,
    org_id: int = Form(...),
    package_id: int = Form(...),
    version_id: int = Form(...),
    quantity: int = Form(...),
    file: UploadFile = File(...),
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> BatchPreviewOut:
    """真实文件导入（I2）：原件私存并按字节计算摘要，服务端解析后走同一预览校验。

    仅支持 UTF-8 文本（CSV/TXT），单文件 ≤ COMMERCE_BATCH_FILE_MAX_BYTES、
    行数 ≤ COMMERCE_BATCH_MAX_ROWS；确认仍只消费服务端已预览数据。
    """
    from app.core.config import settings as app_settings

    content = await file.read(app_settings.COMMERCE_BATCH_FILE_MAX_BYTES + 1)
    if len(content) > app_settings.COMMERCE_BATCH_FILE_MAX_BYTES:
        raise HTTPException(
            status_code=413, detail=f"名单文件超过上限 {app_settings.COMMERCE_BATCH_FILE_MAX_BYTES // 1024} KiB"
        )
    rows = svc.parse_batch_file(content, max_rows=app_settings.COMMERCE_BATCH_MAX_ROWS)
    digest = hashlib.sha256(content).hexdigest()
    original_name = (file.filename or "名单.txt")[:255]

    # 原件写入私有目录（含手机号，不入公开 uploads）
    batch_dir = configured_storage_path(app_settings.COMMERCE_BATCH_DIR)
    batch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stored = f"{secrets.token_hex(16)}.txt"
    (batch_dir / stored).write_bytes(content)

    await audit(
        db,
        action="commerce.batch.preview",
        resource_type="batch",
        detail={
            "org_id": org_id,
            "package_id": package_id,
            "version_id": version_id,
            "quantity": quantity,
            "file_name": original_name,
            "file_sha256": digest,
            "row_count": len(rows),
            "import": "file",
        },
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    batch, batch_rows, summary = await svc.preview_batch(
        db,
        uploaded_by_id=admin.id,
        org_id=org_id,
        package_id=package_id,
        version_id=version_id,
        quantity=quantity,
        file_name=original_name,
        raw_rows=rows,
        file_digest=digest,
        stored_filename=stored,
    )
    batch_out = await _batch_out(db, batch, rows=batch_rows)
    return BatchPreviewOut(batch=batch_out, rows=_batch_rows_out(batch_rows), summary=summary)


@router.get("/batches/{batch_id}/file")
async def admin_batch_file(
    batch_id: int,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    """下载名单原件（含手机号）：鉴权 + 审计 + 禁缓存。"""
    from fastapi.responses import Response as RawResponse

    from app.core.config import settings as app_settings

    batch = await _admin_batch(db, batch_id)
    if not batch.stored_filename:
        raise HTTPException(status_code=404, detail="该批次没有保存原件（JSON 预览无原件）")
    path = configured_storage_path(app_settings.COMMERCE_BATCH_DIR) / batch.stored_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="名单原件不存在")
    await audit(
        db,
        action="commerce.batch.file_download",
        resource_type="batch",
        resource_id=batch.id,
        detail={"batch_no": batch.batch_no},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    return RawResponse(
        content=path.read_bytes(),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="batch-{batch.batch_no}.txt"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/batches", response_model=Page[BatchOut])
async def admin_list_batches(
    org_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> Page[BatchOut]:
    page, page_size = pagination
    filters = []
    if org_id:
        filters.append(MpPurchaseBatch.org_id == org_id)
    if status:
        filters.append(MpPurchaseBatch.status == status)
    total = (
        await db.execute(
            select(func.count()).select_from(MpPurchaseBatch).where(*filters)
        )
    ).scalar()
    batches = (
        await db.execute(
            select(MpPurchaseBatch).where(*filters).order_by(MpPurchaseBatch.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    items = []
    for batch in batches:
        rows = (
            await db.execute(select(MpBatchRow).where(MpBatchRow.batch_id == batch.id))
        ).scalars().all()
        items.append(await _batch_out(db, batch, rows=rows))
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


async def _admin_batch(db: AsyncSession, batch_id: int) -> MpPurchaseBatch:
    batch = await db.get(MpPurchaseBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


@router.get("/batches/{batch_id}", response_model=BatchDetailOut)
async def admin_batch_detail(
    batch_id: int,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> BatchDetailOut:
    batch = await _admin_batch(db, batch_id)
    rows = (
        await db.execute(
            select(MpBatchRow).where(MpBatchRow.batch_id == batch.id).order_by(MpBatchRow.row_no.asc())
        )
    ).scalars().all()
    return await _batch_out(db, batch, rows=rows, with_rows=True)


@router.post("/batches/{batch_id}/confirm", response_model=BatchOut)
async def admin_confirm_batch(
    batch_id: int,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_CONFIRM)),
    db: AsyncSession = Depends(get_db),
) -> BatchOut:
    batch = await _admin_batch(db, batch_id)
    await audit(
        db,
        action="commerce.batch.confirm",
        resource_type="batch",
        resource_id=batch.id,
        detail={"batch_no": batch.batch_no},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    batch = await svc.confirm_batch(db, confirmed_by_id=admin.id, batch=batch)
    rows = (
        await db.execute(select(MpBatchRow).where(MpBatchRow.batch_id == batch.id))
    ).scalars().all()
    return await _batch_out(db, batch, rows=rows)


@router.post("/batches/{batch_id}/cancel", response_model=BatchOut)
async def admin_cancel_batch(
    batch_id: int,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_CONFIRM)),
    db: AsyncSession = Depends(get_db),
) -> BatchOut:
    batch = await _admin_batch(db, batch_id)
    await audit(
        db,
        action="commerce.batch.cancel",
        resource_type="batch",
        resource_id=batch.id,
        detail={"batch_no": batch.batch_no},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    batch = await svc.cancel_batch(db, batch)
    rows = (
        await db.execute(select(MpBatchRow).where(MpBatchRow.batch_id == batch.id))
    ).scalars().all()
    return await _batch_out(db, batch, rows=rows)


# ───────────────────────── 权益异常处理 ─────────────────────────


@router.get("/entitlements", response_model=Page[AdminEntitlementOut])
async def admin_list_entitlements(
    status: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> Page[AdminEntitlementOut]:
    page, page_size = pagination
    filters = []
    if status:
        filters.append(MpEntitlement.status == status)
    if source_type:
        filters.append(MpEntitlement.source_type == source_type)
    if q:
        like = f"%{q}%"
        filters.append(or_(MpEntitlement.phone.like(like), MpEntitlement.package_name.like(like)))
    total = (
        await db.execute(
            select(func.count()).select_from(MpEntitlement).where(*filters)
        )
    ).scalar()
    rows = (
        await db.execute(
            select(MpEntitlement).where(*filters).order_by(MpEntitlement.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[await _admin_entitlement_out(db, e) for e in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/entitlements/{ent_id}/events")
async def admin_entitlement_events(
    ent_id: int,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    ent = await db.get(MpEntitlement, ent_id)
    if ent is None:
        raise HTTPException(status_code=404, detail="权益不存在")
    rows = (
        await db.execute(
            select(MpEntitlementEvent)
            .where(MpEntitlementEvent.entitlement_id == ent.id)
            .order_by(MpEntitlementEvent.id.desc())
            .limit(100)
        )
    ).scalars().all()
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "detail": e.detail,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "created_at": e.created_at,
        }
        for e in rows
    ]


@router.post("/entitlements/{ent_id}/void", response_model=AdminEntitlementOut)
async def admin_void_entitlement(
    ent_id: int,
    body: EntitlementVoidIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_CONFIRM)),
    db: AsyncSession = Depends(get_db),
) -> AdminEntitlementOut:
    from sqlalchemy import update
    from app.services.commerce_service import now_utc

    ent = await db.get(MpEntitlement, ent_id)
    if ent is None:
        raise HTTPException(status_code=404, detail="权益不存在")
    result = await db.execute(
        update(MpEntitlement)
        .where(
            MpEntitlement.id == ent.id,
            MpEntitlement.status.in_([EntitlementStatus.PENDING.value, EntitlementStatus.ACTIVE.value]),
        )
        .values(status=EntitlementStatus.VOID.value, void_reason=body.reason, voided_at=now_utc())
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该权益当前状态不可作废")
    db.add(
        MpEntitlementEvent(
            entitlement_id=ent.id,
            event_type="voided",
            detail={"reason": body.reason},
            actor_type="admin",
            actor_id=admin.id,
        )
    )
    await audit(
        db,
        action="commerce.entitlement.void",
        resource_type="entitlement",
        resource_id=ent.id,
        detail={"reason": body.reason},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该单位此版本已有此号码的权益（并发改派）")
    await db.refresh(ent)
    return await _admin_entitlement_out(db, ent)


@router.post("/entitlements/{ent_id}/reassign", response_model=AdminEntitlementOut)
async def admin_reassign_entitlement(
    ent_id: int,
    body: EntitlementReassignIn,
    request: Request,
    admin: AdminUser = Depends(require_permission(COMMERCE_BATCH_CONFIRM)),
    db: AsyncSession = Depends(get_db),
) -> AdminEntitlementOut:
    ent = (
        await db.execute(select(MpEntitlement).where(MpEntitlement.id == ent_id).with_for_update())
    ).scalar_one_or_none()
    if ent is None:
        raise HTTPException(status_code=404, detail="权益不存在")
    if ent.source_type != EntitlementSource.BATCH.value:
        raise HTTPException(status_code=409, detail="仅单位分配权益可改号")
    if ent.status not in (EntitlementStatus.PENDING.value, EntitlementStatus.REJECTED.value):
        raise HTTPException(status_code=409, detail="该权益当前状态不可改号")
    phone = svc.normalize_phone(body.phone)
    if phone is None:
        raise HTTPException(status_code=422, detail="手机号格式不正确")
    # 同单位同版本防重复（UNIQUE 只挡批次行，跨批次由服务层校验）
    batch = await db.get(MpPurchaseBatch, ent.batch_id)
    conflict = (
        await db.execute(
            select(func.count())
            .select_from(MpEntitlement)
            .join(MpPurchaseBatch, MpEntitlement.batch_id == MpPurchaseBatch.id)
            .where(
                MpPurchaseBatch.org_id == batch.org_id,
                MpPurchaseBatch.package_version_id == ent.package_version_id,
                MpEntitlement.phone == phone,
                MpEntitlement.status != EntitlementStatus.VOID.value,
                MpEntitlement.id != ent.id,
            )
        )
    ).scalar()
    if int(conflict or 0) > 0:
        raise HTTPException(status_code=409, detail="该单位此版本已有此号码的权益")
    old_phone = ent.phone
    ent.phone = phone
    db.add(
        MpEntitlementEvent(
            entitlement_id=ent.id,
            event_type="reassigned",
            detail={"reason": body.reason, "phone_to": mask_phone(phone)},
            actor_type="admin",
            actor_id=admin.id,
        )
    )
    await audit(
        db,
        action="commerce.entitlement.reassign",
        resource_type="entitlement",
        resource_id=ent.id,
        detail={"reason": body.reason, "phone_to": mask_phone(phone), "phone_from": mask_phone(old_phone)},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="该单位此版本已有此号码的权益（并发改派）")
    await db.refresh(ent)
    return await _admin_entitlement_out(db, ent)
