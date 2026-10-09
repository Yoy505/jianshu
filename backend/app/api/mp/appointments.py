"""小程序 · 预约（五步闭环·第四步）：下单、列表、取消、评价。"""

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, get_db, get_redis, pagination_params
from app.models import (
    MP_PERIODS,
    MpAppointment,
    MpAppointmentStatus,
    MpProvider,
    MpProviderService,
    MpReview,
    MpService,
    MpServiceType,
    User,
)
from app.schemas.common import Page
from app.schemas.mp import (
    AppointmentCreate,
    AppointmentOut,
    AppointmentProviderRef,
    AppointmentReviewCreate,
    AppointmentServiceRef,
)
from redis.asyncio import Redis

from app.services.rate_limit import check_user_rate_limit

router = APIRouter()

APPOINT_DAYS = 7  # 契约：date 为今起 7 天内
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
CANCELLABLE_STATUSES = (MpAppointmentStatus.PENDING.value, MpAppointmentStatus.CONFIRMED.value)


async def _load_service_and_provider(
    db: AsyncSession, service_id: int, provider_id: int
) -> tuple[MpService, MpProvider]:
    service = await db.get(MpService, service_id)
    if service is None or not service.on_shelf:
        raise HTTPException(status_code=404, detail="服务不存在或已下架")
    provider = await db.get(MpProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    linked = await db.scalar(
        select(func.count())
        .select_from(MpProviderService)
        .where(
            MpProviderService.service_id == service.id,
            MpProviderService.provider_id == provider.id,
        )
    )
    if int(linked or 0) == 0:
        raise HTTPException(status_code=400, detail="该人员暂不提供此项服务")
    return service, provider


@router.post("/appointments", status_code=201)
async def create_appointment(
    body: AppointmentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    # 没有真实排班和人员核验流程时，预发布及生产环境不得接受假预约。
    if settings.APP_ENV not in {"dev", "test"}:
        raise HTTPException(status_code=503, detail="预约服务尚未开放，请等待真实排班接入")
    await check_user_rate_limit(redis, user_id=user.id, action="mp.appointments", limit=10)
    service, _ = await _load_service_and_provider(db, body.service_id, body.provider_id)

    # 日期：今起 7 天内（含今天）
    today = date.today()
    offset_days = (body.date - today).days
    if offset_days < 0 or offset_days >= APPOINT_DAYS:
        raise HTTPException(status_code=400, detail="预约日期需在今起 7 天内")
    if body.period not in MP_PERIODS:
        raise HTTPException(status_code=400, detail="时段需为上午、下午或晚间")

    # 线上服务地址可空；线下上门 / 三方到店需填写地址
    if service.service_type != MpServiceType.ONLINE.value and not (body.address or "").strip():
        raise HTTPException(status_code=400, detail="线下或三方服务需填写服务地址")

    if not (body.contact_name or "").strip():
        raise HTTPException(status_code=400, detail="联系人姓名不能为空")
    if not PHONE_RE.match(body.contact_phone or ""):
        raise HTTPException(status_code=400, detail="联系手机号格式不正确")

    # 防重复预约：同人同日同时段已有进行中的预约
    # （应用层预检给可读错误；并发竞态由数据库约束兜底——
    #   PG 部分唯一索引 / SQLite 触发器，见迁移 a1c4f7e2b9d8）
    duplicate = (
        await db.execute(
            select(MpAppointment.id).where(
                MpAppointment.user_id == user.id,
                MpAppointment.appt_date == body.date,
                MpAppointment.period == body.period,
                MpAppointment.status.in_(
                    [MpAppointmentStatus.PENDING.value, MpAppointmentStatus.CONFIRMED.value]
                ),
            )
        )
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该时段已有进行中的预约")

    appointment = MpAppointment(
        user_id=user.id,
        service_id=service.id,
        provider_id=body.provider_id,
        appt_date=body.date,
        period=body.period,
        address=(body.address or "").strip() or None,
        contact_name=body.contact_name.strip(),
        contact_phone=body.contact_phone.strip(),
        status=MpAppointmentStatus.PENDING.value,
        remark=(body.remark or "").strip() or None,
    )
    db.add(appointment)
    try:
        await db.commit()
    except IntegrityError:
        # 并发下另一请求先写入同唯一槽位：约束拒绝，映射回同语义 409
        await db.rollback()
        raise HTTPException(status_code=409, detail="该时段已有进行中的预约")
    return {"id": appointment.id, "status": appointment.status}


@router.get("/appointments", response_model=Page[AppointmentOut])
async def list_appointments(
    status: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[AppointmentOut]:
    page, page_size = pagination
    valid_statuses = tuple(s.value for s in MpAppointmentStatus)
    if status is not None and status not in valid_statuses:
        raise HTTPException(status_code=400, detail="预约状态取值不正确")

    filters = [MpAppointment.user_id == user.id]
    if status is not None:
        filters.append(MpAppointment.status == status)

    total = await db.scalar(
        select(func.count()).select_from(MpAppointment).where(*filters)
    )
    rows = (
        await db.execute(
            select(MpAppointment, MpService, MpProvider)
            .join(MpService, MpAppointment.service_id == MpService.id)
            .join(MpProvider, MpAppointment.provider_id == MpProvider.id)
            .where(*filters)
            .order_by(MpAppointment.created_at.desc(), MpAppointment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return Page(
        items=[_appointment_out(appointment, service, provider) for appointment, service, provider in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


def _appointment_out(appointment: MpAppointment, service: MpService, provider: MpProvider) -> AppointmentOut:
    return AppointmentOut(
        id=appointment.id,
        status=appointment.status,
        date=appointment.appt_date,
        period=appointment.period,
        address=appointment.address,
        contact_name=appointment.contact_name,
        contact_phone=appointment.contact_phone,
        remark=appointment.remark,
        service=AppointmentServiceRef(
            id=service.id,
            name=service.name,
            service_type=service.service_type,
            price=service.price,
            unit=service.unit,
        ),
        provider=AppointmentProviderRef(
            id=provider.id,
            name=provider.name,
            title=provider.title,
            avatar=provider.avatar,
        ),
        created_at=appointment.created_at,
    )


async def _get_own_appointment(db: AsyncSession, user: User, appointment_id: int) -> MpAppointment:
    appointment = await db.get(MpAppointment, appointment_id)
    if appointment is None or appointment.user_id != user.id:
        raise HTTPException(status_code=404, detail="预约不存在")
    return appointment


@router.post("/appointments/{appointment_id}/cancel")
async def cancel_appointment(
    appointment_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    appointment = await _get_own_appointment(db, user, appointment_id)
    if appointment.status not in CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail="当前状态不可取消")
    appointment.status = MpAppointmentStatus.CANCELLED.value
    await db.commit()
    return {"id": appointment.id, "status": appointment.status}


@router.post("/appointments/{appointment_id}/review")
async def review_appointment(
    appointment_id: int,
    body: AppointmentReviewCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not 1 <= body.rating <= 5:
        raise HTTPException(status_code=400, detail="评分需在 1-5 星之间")

    appointment = await _get_own_appointment(db, user, appointment_id)
    if appointment.status != MpAppointmentStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail="预约尚未完成，不能评价")
    existing = await db.scalar(
        select(func.count())
        .select_from(MpReview)
        .where(MpReview.appointment_id == appointment.id)
    )
    if int(existing or 0) > 0:
        raise HTTPException(status_code=409, detail="该预约已评价")

    review = MpReview(
        appointment_id=appointment.id,
        user_id=user.id,
        provider_id=appointment.provider_id,
        rating=body.rating,
        content=(body.content or "").strip() or None,
    )
    db.add(review)

    # 更新人员评分均值与服务次数：(旧均值*次数 + 新评分) / (次数+1)，保留 1 位小数
    provider = await db.get(MpProvider, appointment.provider_id)
    if provider is not None:
        old_count = provider.service_count or 0
        new_rating = (float(provider.rating) * old_count + body.rating) / (old_count + 1)
        provider.rating = round(new_rating, 1)
        provider.service_count = old_count + 1

    await db.commit()
    return {
        "id": review.id,
        "rating": review.rating,
        "content": review.content,
        "provider_rating": float(provider.rating) if provider is not None else None,
    }
