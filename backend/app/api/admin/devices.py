"""后台 · 设备云数据接口（工作二切片 C，契约 docs/api.md 第五部分 · 八）。

权限：device.read（编辑含）/ device.manage（仅管理员）。无真实厂商 API——
适配器未注册时同步返回 blocked「厂商适配待配置」，不伪造数据。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, pagination_params
from app.core.permissions import require_permission
from app.models import (
    DeviceBinding,
    DeviceMetric,
    DeviceSyncCursor,
    DeviceSyncLog,
    DeviceVendor,
    User,
)
from app.schemas.common import Page, mask_phone
from app.schemas.device import (
    BindingCreateIn,
    BindingOut,
    CursorOut,
    CursorResetIn,
    MetricOut,
    MetricReviewIn,
    SyncLogOut,
    SyncRunOut,
    UnbindIn,
    VendorCreateIn,
    VendorOut,
)
from app.services import device_service as svc

router = APIRouter(prefix="/admin/devices", tags=["admin-devices"])


def _vendor_out(v: DeviceVendor) -> VendorOut:
    return VendorOut(id=v.id, code=v.code, name=v.name, status=v.status, note=v.note, created_at=v.created_at)


async def _binding_out(db: AsyncSession, b: DeviceBinding) -> BindingOut:
    vendor = await db.get(DeviceVendor, b.vendor_id)
    user = await db.get(User, b.user_id)
    cursor = (
        await db.execute(select(DeviceSyncCursor).where(DeviceSyncCursor.binding_id == b.id))
    ).scalar_one_or_none()
    return BindingOut(
        id=b.id,
        user_id=b.user_id,
        user_nickname=user.nickname if user else None,
        user_phone_masked=mask_phone(user.phone) if user else None,
        vendor_id=b.vendor_id,
        vendor_name=vendor.name if vendor else None,
        device_model=b.device_model,
        device_sn=b.device_sn,
        status=b.status,
        bound_at=b.bound_at,
        unbound_at=b.unbound_at,
        unbind_reason=b.unbind_reason,
        cursor=CursorOut(
            cursor=cursor.cursor,
            status=cursor.status,
            failure_count=cursor.failure_count or 0,
            last_success_at=cursor.last_success_at,
            last_attempt_at=cursor.last_attempt_at,
            last_error=cursor.last_error,
        ).model_dump()
        if cursor
        else None,
    )


@router.get("/vendors", response_model=list[VendorOut])
async def list_vendors(
    admin=Depends(require_permission("device.read")),
    db: AsyncSession = Depends(get_db),
) -> list[VendorOut]:
    rows = (
        (await db.execute(select(DeviceVendor).order_by(DeviceVendor.id.desc()))).scalars().all()
    )
    return [_vendor_out(v) for v in rows]


@router.post("/vendors", response_model=VendorOut, status_code=201)
async def create_vendor(
    payload: VendorCreateIn,
    admin=Depends(require_permission("device.manage")),
    db: AsyncSession = Depends(get_db),
) -> VendorOut:
    vendor = await svc.create_vendor(db, admin, payload.code, payload.name, payload.note)
    return _vendor_out(vendor)


@router.get("/bindings", response_model=Page[BindingOut])
async def list_bindings(
    user_id: int | None = Query(default=None),
    status: str | None = Query(default=None, max_length=10),
    q: str | None = Query(default=None, max_length=64),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("device.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[BindingOut]:
    page, page_size = pagination
    filters = []
    if user_id:
        filters.append(DeviceBinding.user_id == user_id)
    if status:
        filters.append(DeviceBinding.status == status)
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(DeviceBinding.device_sn.like(like), User.phone.like(like)))
    count_stmt = select(func.count()).select_from(DeviceBinding).join(
        User, DeviceBinding.user_id == User.id
    )
    list_stmt = select(DeviceBinding).join(User, DeviceBinding.user_id == User.id).order_by(
        DeviceBinding.id.desc()
    )
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar()
    rows = (
        (await db.execute(list_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    )
    items = [await _binding_out(db, b) for b in rows]
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.post("/bindings", response_model=BindingOut, status_code=201)
async def create_binding(
    payload: BindingCreateIn,
    admin=Depends(require_permission("device.manage")),
    db: AsyncSession = Depends(get_db),
) -> BindingOut:
    binding = await svc.create_binding(
        db, admin, payload.user_id, payload.vendor_id, payload.device_model, payload.device_sn
    )
    return await _binding_out(db, binding)


async def _get_binding(db: AsyncSession, binding_id: int) -> DeviceBinding:
    binding = await db.get(DeviceBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="绑定不存在")
    return binding


@router.post("/bindings/{binding_id}/unbind", response_model=BindingOut)
async def unbind_binding(
    binding_id: int,
    payload: UnbindIn,
    admin=Depends(require_permission("device.manage")),
    db: AsyncSession = Depends(get_db),
) -> BindingOut:
    binding = await _get_binding(db, binding_id)
    binding = await svc.unbind(db, admin, binding, payload.reason)
    return await _binding_out(db, binding)


@router.post("/bindings/{binding_id}/sync", response_model=SyncRunOut)
async def trigger_sync(
    binding_id: int,
    admin=Depends(require_permission("device.manage")),
    db: AsyncSession = Depends(get_db),
) -> SyncRunOut:
    result = await svc.sync_binding(db, binding_id, trigger="manual")
    return SyncRunOut(**result)


@router.post("/bindings/{binding_id}/cursor/reset", response_model=BindingOut)
async def reset_cursor(
    binding_id: int,
    payload: CursorResetIn,
    admin=Depends(require_permission("device.manage")),
    db: AsyncSession = Depends(get_db),
) -> BindingOut:
    binding = await _get_binding(db, binding_id)
    await svc.reset_cursor(db, admin, binding, payload.reason, back=payload.back)
    return await _binding_out(db, binding)


@router.get("/sync-logs", response_model=Page[SyncLogOut])
async def list_sync_logs(
    binding_id: int | None = Query(default=None),
    status: str | None = Query(default=None, max_length=10),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("device.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[SyncLogOut]:
    page, page_size = pagination
    filters = []
    if binding_id:
        filters.append(DeviceSyncLog.binding_id == binding_id)
    if status:
        filters.append(DeviceSyncLog.status == status)
    count_stmt = select(func.count()).select_from(DeviceSyncLog)
    list_stmt = select(DeviceSyncLog).order_by(DeviceSyncLog.id.desc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar()
    rows = (
        (await db.execute(list_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    )
    items = [
        SyncLogOut(
            id=log.id, binding_id=log.binding_id, status=log.status, pulled=log.pulled,
            stored=log.stored, duplicates=log.duplicates, revisions=log.revisions,
            invalid=log.invalid, late=log.late, wrong_user=log.wrong_user,
            detail=log.detail, started_at=log.started_at, finished_at=log.finished_at,
        )
        for log in rows
    ]
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.get("/metrics", response_model=Page[MetricOut])
async def list_metrics(
    binding_id: int | None = Query(default=None),
    quality: str | None = Query(default=None, max_length=16),
    business_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("device.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[MetricOut]:
    from datetime import date as date_cls

    page, page_size = pagination
    filters = []
    if binding_id:
        filters.append(DeviceMetric.binding_id == binding_id)
    if quality:
        filters.append(DeviceMetric.quality == quality)
    if business_date:
        filters.append(DeviceMetric.business_date == date_cls.fromisoformat(business_date))
    count_stmt = select(func.count()).select_from(DeviceMetric)
    list_stmt = select(DeviceMetric).order_by(DeviceMetric.id.desc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar()
    rows = (
        (await db.execute(list_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    )
    items = [
        MetricOut(
            id=m.id, binding_id=m.binding_id, user_id=m.user_id, metric_type=m.metric_type,
            value=float(m.value), unit=m.unit, business_date=m.business_date,
            tz_offset_minutes=m.tz_offset_minutes, vendor_record_id=m.vendor_record_id,
            vendor_updated_at=m.vendor_updated_at, received_at=m.received_at,
            quality=m.quality, revision=m.revision, review_note=m.review_note,
        )
        for m in rows
    ]
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.post("/metrics/{metric_id}/review", response_model=MetricOut)
async def review_metric(
    metric_id: int,
    payload: MetricReviewIn,
    admin=Depends(require_permission("device.manage")),
    db: AsyncSession = Depends(get_db),
) -> MetricOut:
    metric = await db.get(DeviceMetric, metric_id)
    if metric is None:
        raise HTTPException(status_code=404, detail="数据点不存在")
    metric = await svc.review_metric(db, admin, metric, payload.reason)
    return MetricOut(
        id=metric.id, binding_id=metric.binding_id, user_id=metric.user_id,
        metric_type=metric.metric_type, value=float(metric.value), unit=metric.unit,
        business_date=metric.business_date, tz_offset_minutes=metric.tz_offset_minutes,
        vendor_record_id=metric.vendor_record_id, vendor_updated_at=metric.vendor_updated_at,
        received_at=metric.received_at, quality=metric.quality, revision=metric.revision,
        review_note=metric.review_note,
    )
