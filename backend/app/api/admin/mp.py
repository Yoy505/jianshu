"""后台 · 健康域管理（/admin/mp，契约见 docs/api.md 第三部分）。

状态变更全部经 audit_service 与业务同事务写入审计（action 前缀 mp.）。
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import String, cast, func, join, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db, pagination_params
from app.core.permissions import MP_MANAGE, require_permission
from app.models import (
    MP_DRUG_TAGS,
    MP_DRUG_PUBLICATION_STATUSES,
    AdminUser,
    MpAppointment,
    MpAppointmentStatus,
    MpConsult,
    MpConsultMessage,
    MpConsultStatus,
    MpDrug,
    MpDrugLeaflet,
    MpDrugFeedback,
    MpNotice,
    MpProduct,
    MpProvider,
    MpProviderService,
    MpScenario,
    MpService,
    MpServiceType,
    User,
)
from app.schemas.common import Page, mask_phone
from app.schemas.mp import (
    ConsultProviderRef,
    ConsultServiceRef,
    DrugBoardItemOut,
    DrugOut,
    ProductOut,
    ProviderOut,
)
from app.schemas.mp_admin import (
    AppointmentAdminOut,
    ServiceAdminOut,
    AppointmentUserRef,
    ConsultAdminDetail,
    ConsultAdminOut,
    ConsultMessageAdminOut,
    ConsultUserRef,
    DrugAdminDetail,
    DrugLeafletAdminOut,
    MpAdminStats,
    NoticeCreate,
    NoticeOut,
    ProviderUpdate,
    ServiceUpdate,
)
from app.services.audit_service import audit

router = APIRouter(prefix="/admin/mp", tags=["admin-mp"])

_VALID_APPT_STATUS = {status.value for status in MpAppointmentStatus}
_VALID_CONSULT_STATUS = {status.value for status in MpConsultStatus}
_VALID_SERVICE_TYPE = {status.value for status in MpServiceType}


# ───────────────────────── 概览 ─────────────────────────

@router.get("/stats", response_model=MpAdminStats)
async def mp_stats(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> MpAdminStats:
    appt_rows = (
        await db.execute(
            select(MpAppointment.status, func.count()).group_by(MpAppointment.status)
        )
    ).all()
    appointments = {status.value: 0 for status in MpAppointmentStatus}
    total = 0
    for status, count in appt_rows:
        appointments[status] = int(count)
        total += int(count)

    async def _count(model, *filters) -> int:
        return int(await db.scalar(select(func.count()).select_from(model).where(*filters)) or 0)

    return MpAdminStats(
        appointments={**appointments, "total": total},
        consults_open=await _count(MpConsult, MpConsult.status == MpConsultStatus.OPEN.value),
        providers_total=await _count(MpProvider),
        providers_online=await _count(MpProvider, MpProvider.online.is_(True)),
        services_total=await _count(MpService),
        drugs_total=await _count(MpDrug),
        products_total=await _count(MpProduct),
    )


# ───────────────────────── 预约管理 ─────────────────────────

def _appt_out(appt: MpAppointment, user: User, service: MpService, provider: MpProvider) -> AppointmentAdminOut:
    return AppointmentAdminOut(
        id=appt.id,
        user=AppointmentUserRef(
            id=user.id, nickname=user.nickname or "用户", phone=mask_phone(user.phone)
        ),
        service={  # type: ignore[arg-type]  # 与 C 端同构字段
            "id": service.id,
            "name": service.name,
            "service_type": service.service_type,
            "price": service.price,
            "unit": service.unit,
        },
        provider={"id": provider.id, "name": provider.name, "title": provider.title, "avatar": provider.avatar},  # type: ignore[arg-type]
        appt_date=appt.appt_date,
        period=appt.period,
        address=appt.address,
        contact_name=appt.contact_name,
        contact_phone=appt.contact_phone,
        remark=appt.remark,
        status=appt.status,
        created_at=appt.created_at,
    )


async def _load_appt_row(db: AsyncSession, appointment_id: int):
    row = (
        await db.execute(
            select(MpAppointment, User, MpService, MpProvider)
            .join(User, MpAppointment.user_id == User.id)
            .join(MpService, MpAppointment.service_id == MpService.id)
            .join(MpProvider, MpAppointment.provider_id == MpProvider.id)
            .where(MpAppointment.id == appointment_id)
        )
    ).first()
    return row


@router.get("/appointments", response_model=Page[AppointmentAdminOut])
async def list_appointments(
    status: str | None = Query(default=None),
    date_: date | None = Query(default=None, alias="date"),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[AppointmentAdminOut]:
    page, page_size = pagination
    if status is not None and status not in _VALID_APPT_STATUS:
        raise HTTPException(status_code=400, detail="无效的预约状态")

    filters = []
    if status:
        filters.append(MpAppointment.status == status)
    if date_ is not None:
        filters.append(MpAppointment.appt_date == date_)
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                MpAppointment.contact_name.ilike(pattern),
                MpAppointment.contact_phone.ilike(pattern),
                MpProvider.name.ilike(pattern),
                MpService.name.ilike(pattern),
            )
        )

    base = (
        select(MpAppointment, User, MpService, MpProvider)
        .join(User, MpAppointment.user_id == User.id)
        .join(MpService, MpAppointment.service_id == MpService.id)
        .join(MpProvider, MpAppointment.provider_id == MpProvider.id)
    )
    appt_join = join(
        join(
            join(MpAppointment, User, MpAppointment.user_id == User.id),
            MpService,
            MpAppointment.service_id == MpService.id,
        ),
        MpProvider,
        MpAppointment.provider_id == MpProvider.id,
    )
    total = await db.scalar(select(func.count()).select_from(appt_join).where(*filters))
    rows = (
        await db.execute(
            base.where(*filters)
            .order_by(MpAppointment.created_at.desc(), MpAppointment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[_appt_out(appt, user, service, provider) for appt, user, service, provider in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


async def _transition_appointment(
    db: AsyncSession,
    appointment_id: int,
    action: str,
    target: str,
    allowed_from: set[str],
    *,
    admin: AdminUser,
    request: Request,
) -> AppointmentAdminOut:
    row = await _load_appt_row(db, appointment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="预约不存在")
    appt, user, service, provider = row
    if appt.status not in allowed_from:
        raise HTTPException(status_code=409, detail=f"当前状态（{appt.status}）不允许该操作")
    previous = appt.status
    appt.status = target
    await audit(
        db,
        action=action,
        resource_type="mp_appointment",
        resource_id=appt.id,
        detail={
            "from": previous,
            "to": target,
            "service": service.name,
            "provider": provider.name,
            "appt_date": str(appt.appt_date),
            "period": appt.period,
        },
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    row = await _load_appt_row(db, appointment_id)
    return _appt_out(*row)


@router.post("/appointments/{appointment_id}/confirm", response_model=AppointmentAdminOut)
async def confirm_appointment(
    appointment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> AppointmentAdminOut:
    return await _transition_appointment(
        db,
        appointment_id,
        "mp.appointment.confirm",
        MpAppointmentStatus.CONFIRMED.value,
        {MpAppointmentStatus.PENDING.value},
        admin=admin,
        request=request,
    )


@router.post("/appointments/{appointment_id}/complete", response_model=AppointmentAdminOut)
async def complete_appointment(
    appointment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> AppointmentAdminOut:
    return await _transition_appointment(
        db,
        appointment_id,
        "mp.appointment.complete",
        MpAppointmentStatus.COMPLETED.value,
        {MpAppointmentStatus.CONFIRMED.value},
        admin=admin,
        request=request,
    )


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentAdminOut)
async def cancel_appointment(
    appointment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> AppointmentAdminOut:
    return await _transition_appointment(
        db,
        appointment_id,
        "mp.appointment.cancel",
        MpAppointmentStatus.CANCELLED.value,
        {MpAppointmentStatus.PENDING.value, MpAppointmentStatus.CONFIRMED.value},
        admin=admin,
        request=request,
    )


# ───────────────────────── 咨询管理 ─────────────────────────

def _consult_out(consult: MpConsult, user: User, service: MpService, provider: MpProvider, message_count: int, last_content: str | None) -> ConsultAdminOut:
    return ConsultAdminOut(
        id=consult.id,
        user=ConsultUserRef(id=user.id, nickname=user.nickname or "用户"),
        service=ConsultServiceRef(id=service.id, name=service.name, service_type=service.service_type),
        provider=ConsultProviderRef(
            id=provider.id, name=provider.name, title=provider.title, avatar=provider.avatar, online=provider.online
        ),
        status=consult.status,
        message_count=message_count,
        last_message=last_content,
        last_message_at=consult.last_message_at,
        created_at=consult.created_at,
    )


@router.get("/consults", response_model=Page[ConsultAdminOut])
async def list_consults(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[ConsultAdminOut]:
    page, page_size = pagination
    if status is not None and status not in _VALID_CONSULT_STATUS:
        raise HTTPException(status_code=400, detail="无效的会话状态")

    filters = []
    if status:
        filters.append(MpConsult.status == status)
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(User.nickname.ilike(pattern), MpProvider.name.ilike(pattern), MpService.name.ilike(pattern))
        )

    consult_join = join(
        join(
            join(MpConsult, User, MpConsult.user_id == User.id),
            MpService,
            MpConsult.service_id == MpService.id,
        ),
        MpProvider,
        MpConsult.provider_id == MpProvider.id,
    )
    total = await db.scalar(select(func.count()).select_from(consult_join).where(*filters))
    rows = (
        await db.execute(
            select(MpConsult, User, MpService, MpProvider, MpConsultMessage.content)
            .join(User, MpConsult.user_id == User.id)
            .join(MpService, MpConsult.service_id == MpService.id)
            .join(MpProvider, MpConsult.provider_id == MpProvider.id)
            .outerjoin(
                MpConsultMessage,
                MpConsultMessage.id
                == select(func.max(MpConsultMessage.id))
                .where(MpConsultMessage.consult_id == MpConsult.id)
                .correlate(MpConsult)
                .scalar_subquery(),
            )
            .where(*filters)
            .order_by(MpConsult.last_message_at.desc().nullslast(), MpConsult.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for consult, user, service, provider, last_content in rows:
        count = await db.scalar(
            select(func.count())
            .select_from(MpConsultMessage)
            .where(MpConsultMessage.consult_id == consult.id)
        )
        items.append(_consult_out(consult, user, service, provider, int(count or 0), last_content))
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.get("/consults/{consult_id}", response_model=ConsultAdminDetail)
async def get_consult(
    consult_id: int,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> ConsultAdminDetail:
    row = (
        await db.execute(
            select(MpConsult, User, MpService, MpProvider)
            .join(User, MpConsult.user_id == User.id)
            .join(MpService, MpConsult.service_id == MpService.id)
            .join(MpProvider, MpConsult.provider_id == MpProvider.id)
            .where(MpConsult.id == consult_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    consult, user, service, provider = row
    messages = (
        await db.execute(
            select(MpConsultMessage)
            .where(MpConsultMessage.consult_id == consult_id)
            .order_by(MpConsultMessage.id.desc())
            .limit(100)
        )
    ).scalars().all()
    count = await db.scalar(
        select(func.count()).select_from(MpConsultMessage).where(MpConsultMessage.consult_id == consult_id)
    )
    last = messages[0] if messages else None
    base = _consult_out(
        consult, user, service, provider, int(count or 0), last.content if last else None
    )
    return ConsultAdminDetail(
        **base.model_dump(),
        messages=[
            ConsultMessageAdminOut(
                id=m.id, sender=m.sender, content=m.content, created_at=m.created_at
            )
            for m in reversed(messages)  # 正序返回
        ],
    )


@router.post("/consults/{consult_id}/close", response_model=ConsultAdminDetail)
async def close_consult(
    consult_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> ConsultAdminDetail:
    row = (
        await db.execute(
            select(MpConsult, User, MpService, MpProvider)
            .join(User, MpConsult.user_id == User.id)
            .join(MpService, MpConsult.service_id == MpService.id)
            .join(MpProvider, MpConsult.provider_id == MpProvider.id)
            .where(MpConsult.id == consult_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    consult = row[0]
    if consult.status != MpConsultStatus.OPEN.value:
        raise HTTPException(status_code=409, detail="会话已关闭")
    consult.status = MpConsultStatus.CLOSED.value
    await audit(
        db,
        action="mp.consult.close",
        resource_type="mp_consult",
        resource_id=consult.id,
        detail={"provider": row[3].name, "service": row[2].name},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    return await get_consult(consult_id, db, admin)


# ───────────────────────── 专业人员管理 ─────────────────────────

def _provider_out(provider: MpProvider) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        gender=provider.gender,
        title=provider.title,
        dept=provider.dept,
        org=provider.org,
        years=provider.years,
        skills=provider.skills or [],
        intro=provider.intro,
        avatar=provider.avatar,
        rating=provider.rating,
        service_count=provider.service_count,
        online=provider.online,
    )


@router.get("/providers", response_model=Page[ProviderOut])
async def list_providers(
    q: str | None = Query(default=None),
    online: bool | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[ProviderOut]:
    page, page_size = pagination
    filters = []
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(MpProvider.name.ilike(pattern), MpProvider.dept.ilike(pattern), MpProvider.org.ilike(pattern))
        )
    if online is not None:
        filters.append(MpProvider.online.is_(online))

    total = await db.scalar(select(func.count()).select_from(MpProvider).where(*filters))
    rows = (
        await db.execute(
            select(MpProvider)
            .where(*filters)
            .order_by(MpProvider.rating.desc(), MpProvider.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[_provider_out(provider) for provider in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> ProviderOut:
    provider = await db.get(MpProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    data = body.model_dump(exclude_unset=True)
    if data:
        # 审计只记变更字段名与关键业务位
        audit_detail = {
            field: (value if field in {"online", "years"} else "(updated)")
            for field, value in data.items()
        }
        for field, value in data.items():
            setattr(provider, field, value)
        await audit(
            db,
            action="mp.provider.update",
            resource_type="mp_provider",
            resource_id=provider.id,
            detail=audit_detail,
            actor_id=admin.id,
            actor_name=admin.name,
            request=request,
        )
        await db.commit()
        await db.refresh(provider)
    return _provider_out(provider)


# ───────────────────────── 服务与商城管理 ─────────────────────────

def _service_out(service: MpService, scenario: MpScenario, provider_count: int) -> ServiceAdminOut:
    return ServiceAdminOut(
        id=service.id,
        scenario=service.scenario_key,
        scenario_name=scenario.name,
        service_type=service.service_type,
        name=service.name,
        desc=service.desc,
        price=service.price,
        unit=service.unit,
        duration_min=service.duration_min,
        sales=service.sales,
        provider_count=provider_count,
        on_shelf=service.on_shelf,
    )


async def _load_service_row(db: AsyncSession, service_id: int):
    return (
        await db.execute(
            select(MpService, MpScenario)
            .join(MpScenario, MpService.scenario_key == MpScenario.key)
            .where(MpService.id == service_id)
        )
    ).first()


@router.get("/services", response_model=Page[ServiceAdminOut])
async def list_services(
    scenario: str | None = Query(default=None),
    service_type: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[ServiceAdminOut]:
    page, page_size = pagination
    if service_type is not None and service_type not in _VALID_SERVICE_TYPE:
        raise HTTPException(status_code=400, detail="无效的服务类型")

    filters = []
    if scenario:
        filters.append(MpService.scenario_key == scenario.strip())
    if service_type:
        filters.append(MpService.service_type == service_type)

    base = select(MpService, MpScenario).join(MpScenario, MpService.scenario_key == MpScenario.key)
    total = await db.scalar(
        select(func.count())
        .select_from(join(MpService, MpScenario, MpService.scenario_key == MpScenario.key))
        .where(*filters)
    )
    rows = (
        await db.execute(
            base.where(*filters)
            .order_by(MpService.sales.desc(), MpService.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for service, scenario_row in rows:
        count = await db.scalar(
            select(func.count())
            .select_from(MpProviderService)
            .where(MpProviderService.service_id == service.id)
        )
        items.append(_service_out(service, scenario_row, int(count or 0)))
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.patch("/services/{service_id}", response_model=ServiceAdminOut)
async def update_service(
    service_id: int,
    body: ServiceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> ServiceAdminOut:
    row = await _load_service_row(db, service_id)
    if row is None:
        raise HTTPException(status_code=404, detail="服务不存在")
    service = row[0]
    data = body.model_dump(exclude_unset=True)
    if data:
        audit_detail = {
            field: (value if field in {"price", "on_shelf", "duration_min"} else "(updated)")
            for field, value in data.items()
        }
        for field, value in data.items():
            setattr(service, field, value)
        await audit(
            db,
            action="mp.service.update",
            resource_type="mp_service",
            resource_id=service.id,
            detail=audit_detail,
            actor_id=admin.id,
            actor_name=admin.name,
            request=request,
        )
        await db.commit()
        await db.refresh(service)
    row = await _load_service_row(db, service_id)
    count = await db.scalar(
        select(func.count())
        .select_from(MpProviderService)
        .where(MpProviderService.service_id == service_id)
    )
    return _service_out(row[0], row[1], int(count or 0))


@router.get("/notices", response_model=list[NoticeOut])
async def list_notices(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[NoticeOut]:
    rows = (
        await db.execute(select(MpNotice).order_by(MpNotice.sort.asc(), MpNotice.id.asc()))
    ).scalars().all()
    return [
        NoticeOut(id=n.id, content=n.content, sort=n.sort, created_at=n.created_at) for n in rows
    ]


@router.post("/notices", response_model=NoticeOut, status_code=201)
async def create_notice(
    body: NoticeCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> NoticeOut:
    notice = MpNotice(content=body.content.strip(), sort=body.sort)
    db.add(notice)
    await db.flush()
    await audit(
        db,
        action="mp.notice.create",
        resource_type="mp_notice",
        resource_id=notice.id,
        detail={"content": notice.content[:80]},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.commit()
    await db.refresh(notice)
    return NoticeOut(id=notice.id, content=notice.content, sort=notice.sort, created_at=notice.created_at)


@router.delete("/notices/{notice_id}", status_code=204)
async def delete_notice(
    notice_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> Response:
    notice = await db.get(MpNotice, notice_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    await audit(
        db,
        action="mp.notice.delete",
        resource_type="mp_notice",
        resource_id=notice.id,
        detail={"content": notice.content[:80]},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.delete(notice)
    await db.commit()
    return Response(status_code=204)


@router.get("/products", response_model=Page[ProductOut])
async def list_products(
    category: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[ProductOut]:
    page, page_size = pagination
    filters = []
    if category:
        filters.append(MpProduct.category == category.strip())
    total = await db.scalar(select(func.count()).select_from(MpProduct).where(*filters))
    rows = (
        await db.execute(
            select(MpProduct)
            .where(*filters)
            .order_by(MpProduct.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[
            ProductOut(
                id=p.id, category=p.category, name=p.name, desc=p.desc, price=p.price, status=p.status
            )
            for p in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


# ───────────────────────── 药品展厅管理 ─────────────────────────

def _drug_out(drug: MpDrug) -> DrugOut:
    return DrugOut(
        id=drug.id,
        common_name=drug.common_name,
        approval_no=drug.approval_no,
        specification=drug.specification,
        license_holder=drug.license_holder,
        source_name=drug.source_name,
        source_url=drug.source_url,
        source_checked_at=drug.source_checked_at,
        publication_status=drug.publication_status,
        form=drug.form,
        manufacturer=drug.manufacturer,
        tags=drug.tags or [],
        indication=drug.indication,
        usage_note=drug.usage_note,
        contraindication=drug.contraindication,
        science=drug.science,
        video_url=drug.video_url,
        brand_name=drug.brand_name,
        favorited=False,
    )


_LEAFLET_STATUSES = {"pending", "approved", "published", "withdrawn"}


def _leaflet_out(leaflet: MpDrugLeaflet, drug_name: str) -> DrugLeafletAdminOut:
    # Explicit allowlist: no stored_filename, original_filename, source_url or file bytes.
    return DrugLeafletAdminOut(
        id=leaflet.id,
        drug_id=leaflet.drug_id,
        drug_common_name=drug_name,
        approval_no=leaflet.approval_no,
        source_name=leaflet.source_name,
        source_document_id=leaflet.source_document_id,
        authorization_ref=leaflet.authorization_ref,
        identity_check_method=leaflet.identity_check_method,
        document_date=leaflet.document_date,
        document_version=leaflet.document_version,
        source_checked_at=leaflet.source_checked_at,
        file_size=leaflet.file_size,
        file_sha256=leaflet.file_sha256,
        manifest_sha256=leaflet.manifest_sha256,
        status=leaflet.status,
        created_at=leaflet.created_at,
        updated_at=leaflet.updated_at,
    )


@router.get("/drug-leaflets", response_model=Page[DrugLeafletAdminOut])
async def list_drug_leaflets_admin(
    status: str | None = Query(default=None),
    approval_no: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[DrugLeafletAdminOut]:
    if status is not None and status not in _LEAFLET_STATUSES:
        raise HTTPException(status_code=400, detail="说明书状态无效")
    page, page_size = pagination
    filters = []
    if status:
        filters.append(MpDrugLeaflet.status == status)
    if approval_no:
        filters.append(MpDrugLeaflet.approval_no == approval_no.strip())
    total = await db.scalar(
        select(func.count()).select_from(MpDrugLeaflet).where(*filters)
    )
    rows = (
        await db.execute(
            select(MpDrugLeaflet, MpDrug.common_name)
            .join(MpDrug, MpDrugLeaflet.drug_id == MpDrug.id)
            .where(*filters)
            .order_by(MpDrugLeaflet.created_at.desc(), MpDrugLeaflet.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[_leaflet_out(leaflet, drug_name) for leaflet, drug_name in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/drug-leaflets/{leaflet_id}", response_model=DrugLeafletAdminOut)
async def get_drug_leaflet_admin(
    leaflet_id: int,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> DrugLeafletAdminOut:
    row = (
        await db.execute(
            select(MpDrugLeaflet, MpDrug.common_name)
            .join(MpDrug, MpDrugLeaflet.drug_id == MpDrug.id)
            .where(MpDrugLeaflet.id == leaflet_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="说明书记录不存在")
    return _leaflet_out(*row)


@router.get("/drugs", response_model=Page[DrugOut])
async def list_drugs_admin(
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    publication_status: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> Page[DrugOut]:
    page, page_size = pagination
    if tag is not None and tag not in MP_DRUG_TAGS:
        raise HTTPException(status_code=400, detail="药品标签仅支持 otc、rx、essential 或 insurance")
    if publication_status is not None and publication_status not in MP_DRUG_PUBLICATION_STATUSES:
        raise HTTPException(status_code=400, detail="药品发布状态无效")
    filters = []
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(or_(MpDrug.common_name.ilike(pattern), MpDrug.brand_name.ilike(pattern)))
    if tag:
        # tags 为 JSON 数组，与 C 端一致按文本包含匹配
        filters.append(cast(MpDrug.tags, String).like(f'%"{tag}"%'))
    if publication_status:
        filters.append(MpDrug.publication_status == publication_status)

    total = await db.scalar(select(func.count()).select_from(MpDrug).where(*filters))
    rows = (
        await db.execute(
            select(MpDrug)
            .where(*filters)
            .order_by(MpDrug.common_name.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[_drug_out(drug) for drug in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/drugs/{drug_id}", response_model=DrugAdminDetail)
async def get_drug_admin(
    drug_id: int,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> DrugAdminDetail:
    drug = await db.get(MpDrug, drug_id)
    if drug is None:
        raise HTTPException(status_code=404, detail="药品不存在")
    board_rows = (
        await db.execute(
            select(MpDrugFeedback, User.nickname)
            .join(User, MpDrugFeedback.user_id == User.id)
            .where(MpDrugFeedback.drug_id == drug_id)
            .order_by(MpDrugFeedback.created_at.desc(), MpDrugFeedback.id.desc())
            .limit(50)
        )
    ).all()
    return DrugAdminDetail(
        **_drug_out(drug).model_dump(),
        board=[
            DrugBoardItemOut(
                id=feedback.id,
                user_name=nickname or "匿名用户",
                content=feedback.content,
                created_at=feedback.created_at,
            )
            for feedback, nickname in board_rows
        ],
    )


@router.delete("/drugs/feedback/{feedback_id}", status_code=204)
async def delete_drug_feedback(
    feedback_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_permission(MP_MANAGE)),
) -> Response:
    feedback = await db.get(MpDrugFeedback, feedback_id)
    if feedback is None:
        raise HTTPException(status_code=404, detail="留言不存在")
    drug = await db.get(MpDrug, feedback.drug_id)
    await audit(
        db,
        action="mp.drug.feedback_delete",
        resource_type="mp_drug",
        resource_id=feedback.drug_id,
        detail={"feedback_id": feedback.id, "drug": drug.common_name if drug else None},
        actor_id=admin.id,
        actor_name=admin.name,
        request=request,
    )
    await db.delete(feedback)
    await db.commit()
    return Response(status_code=204)
