"""小程序 · 专业人员匹配：列表、详情（近 7 天可约时段 + 最近评价）。"""

from datetime import date, timedelta
from zlib import crc32

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db, pagination_params
from app.models import MP_PERIODS, MpProvider, MpProviderService, MpReview, MpScenario, MpService, User
from app.schemas.common import Page
from app.schemas.mp import (
    ProviderDetailOut,
    ProviderOut,
    ProviderReviewOut,
    ProviderSlotOut,
)

router = APIRouter()

SLOT_DAYS = 7  # 契约：近 7 天可约时段
SLOT_AVAILABLE_RATIO = 10  # 哈希 % 10 < 6 视为可约（约 6 成时段开放）


def _slot_hash(provider_id: int, day: date, tag: str) -> int:
    """按 人员+日期+时段 确定性哈希：同一输入永远同一结果，重启不漂移。"""
    return crc32(f"{provider_id}|{day.isoformat()}|{tag}".encode("utf-8"))


def build_slots(provider_id: int, today: date | None = None) -> list[ProviderSlotOut]:
    # 真实排班尚未接入。哈希时段仅供本地开发和自动化测试演示。
    if settings.APP_ENV not in {"dev", "test"}:
        return []
    today = today or date.today()
    slots: list[ProviderSlotOut] = []
    for offset in range(SLOT_DAYS):
        day = today + timedelta(days=offset)
        periods = [
            period
            for period in MP_PERIODS
            if _slot_hash(provider_id, day, period) % SLOT_AVAILABLE_RATIO < 6
        ]
        if not periods:
            # 兜底：保证每天至少一个可约时段
            periods = [MP_PERIODS[_slot_hash(provider_id, day, "fallback") % len(MP_PERIODS)]]
        slots.append(ProviderSlotOut(date=day, periods=periods))
    return slots


@router.get("/providers", response_model=Page[ProviderOut])
async def list_providers(
    service_id: int | None = Query(default=None),
    scenario: str | None = Query(default=None),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> Page[ProviderOut]:
    page, page_size = pagination
    if scenario is not None:
        exists = await db.get(MpScenario, scenario)
        if exists is None:
            raise HTTPException(status_code=400, detail="场景不存在")

    filters: list = [True]
    if service_id is not None:
        filters.append(
            MpProvider.id.in_(
                select(MpProviderService.provider_id).where(
                    MpProviderService.service_id == service_id
                )
            )
        )
    if scenario is not None:
        filters.append(
            MpProvider.id.in_(
                select(MpProviderService.provider_id)
                .join(MpService, MpProviderService.service_id == MpService.id)
                .where(MpService.scenario_key == scenario)
            )
        )
    if q:
        keyword = f"%{q.strip()}%"
        filters.append(
            (MpProvider.name.like(keyword))
            | (MpProvider.dept.like(keyword))
            | (cast(MpProvider.skills, String).like(keyword))
        )

    total = await db.scalar(
        select(func.count()).select_from(MpProvider).where(*filters)
    )
    rows = (
        await db.execute(
            select(MpProvider)
            .where(*filters)
            .order_by(MpProvider.online.desc(), MpProvider.rating.desc(), MpProvider.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return Page(
        items=[_provider_out(p) for p in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


def _provider_out(provider: MpProvider) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        gender=provider.gender,
        title=provider.title,
        dept=provider.dept,
        org=provider.org,
        years=provider.years,
        skills=list(provider.skills or []),
        intro=provider.intro,
        avatar=provider.avatar,
        rating=float(provider.rating),
        service_count=provider.service_count,
        online=bool(provider.online),
    )


@router.get("/providers/{provider_id}", response_model=ProviderDetailOut)
async def get_provider(provider_id: int, db: AsyncSession = Depends(get_db)) -> ProviderDetailOut:
    provider = await db.get(MpProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="人员不存在")

    review_rows = (
        await db.execute(
            select(MpReview, User.nickname)
            .join(User, MpReview.user_id == User.id)
            .where(MpReview.provider_id == provider.id)
            .order_by(MpReview.created_at.desc(), MpReview.id.desc())
            .limit(5)
        )
    ).all()

    return ProviderDetailOut(
        **_provider_out(provider).model_dump(),
        slots=build_slots(provider.id),
        reviews=[
            ProviderReviewOut(
                id=review.id,
                user_name=nickname or "匿名用户",
                rating=review.rating,
                content=review.content,
                created_at=review.created_at,
            )
            for review, nickname in review_rows
        ],
    )
