"""小程序 · 场景与服务目录。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, pagination_params
from app.models import MpProviderService, MpScenario, MpService, MpServiceType
from app.schemas.common import Page
from app.schemas.mp import ScenarioOut, ServiceOut

router = APIRouter()

VALID_SERVICE_TYPES = tuple(t.value for t in MpServiceType)


async def _ensure_scenario(db: AsyncSession, scenario_key: str) -> None:
    exists = await db.get(MpScenario, scenario_key)
    if exists is None:
        raise HTTPException(status_code=400, detail="场景不存在")


@router.get("/scenarios", response_model=list[ScenarioOut])
async def list_scenarios(db: AsyncSession = Depends(get_db)) -> list[ScenarioOut]:
    rows = (
        await db.execute(select(MpScenario).order_by(MpScenario.sort, MpScenario.key))
    ).scalars().all()
    return [
        ScenarioOut(key=s.key, name=s.name, tagline=s.tagline, sort=s.sort) for s in rows
    ]


@router.get("/services", response_model=Page[ServiceOut])
async def list_services(
    scenario: str | None = Query(default=None),
    service_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    db: AsyncSession = Depends(get_db),
) -> Page[ServiceOut]:
    page, page_size = pagination
    if service_type is not None and service_type not in VALID_SERVICE_TYPES:
        raise HTTPException(status_code=400, detail="服务类型仅支持 online、offline 或 third")
    if scenario is not None:
        await _ensure_scenario(db, scenario)

    filters = [MpService.on_shelf.is_(True)]
    if scenario is not None:
        filters.append(MpService.scenario_key == scenario)
    if service_type is not None:
        filters.append(MpService.service_type == service_type)
    if q:
        keyword = f"%{q.strip()}%"
        filters.append(
            (MpService.name.like(keyword)) | (MpService.desc.like(keyword))
        )

    total = await db.scalar(select(func.count()).select_from(MpService).where(*filters))
    rows = (
        await db.execute(
            select(MpService, MpScenario.name)
            .join(MpScenario, MpService.scenario_key == MpScenario.key)
            .where(*filters)
            .order_by(MpService.sales.desc(), MpService.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    # 可匹配人员数（按本页服务批量统计）
    service_ids = [service.id for service, _ in rows]
    provider_counts: dict[int, int] = {}
    if service_ids:
        count_rows = (
            await db.execute(
                select(
                    MpProviderService.service_id,
                    func.count(MpProviderService.provider_id),
                )
                .where(MpProviderService.service_id.in_(service_ids))
                .group_by(MpProviderService.service_id)
            )
        ).all()
        provider_counts = {service_id: int(count or 0) for service_id, count in count_rows}

    return Page(
        items=[
            ServiceOut(
                id=service.id,
                scenario=service.scenario_key,
                scenario_name=scenario_name,
                service_type=service.service_type,
                name=service.name,
                desc=service.desc,
                price=service.price,
                unit=service.unit,
                duration_min=service.duration_min,
                sales=service.sales,
                provider_count=provider_counts.get(service.id, 0),
            )
            for service, scenario_name in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/services/{service_id}", response_model=ServiceOut)
async def get_service(service_id: int, db: AsyncSession = Depends(get_db)) -> ServiceOut:
    row = (
        await db.execute(
            select(MpService, MpScenario.name)
            .join(MpScenario, MpService.scenario_key == MpScenario.key)
            .where(MpService.id == service_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="服务不存在")
    service, scenario_name = row
    if not service.on_shelf:
        raise HTTPException(status_code=404, detail="服务已下架")
    provider_count = await db.scalar(
        select(func.count())
        .select_from(MpProviderService)
        .where(MpProviderService.service_id == service.id)
    )
    return ServiceOut(
        id=service.id,
        scenario=service.scenario_key,
        scenario_name=scenario_name,
        service_type=service.service_type,
        name=service.name,
        desc=service.desc,
        price=service.price,
        unit=service.unit,
        duration_min=service.duration_min,
        sales=service.sales,
        provider_count=int(provider_count or 0),
    )
