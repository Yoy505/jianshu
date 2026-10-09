"""小程序 · 首页聚合：九大场景、公告、平台统计。"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.models import MpNotice, MpProvider, MpScenario, MpService, User
from app.schemas.mp import HomeNoticeOut, HomeOut, HomeStatsOut, ScenarioOut

router = APIRouter()


@router.get("/home", response_model=HomeOut)
async def home(db: AsyncSession = Depends(get_db)) -> HomeOut:
    scenarios = (
        await db.execute(select(MpScenario).order_by(MpScenario.sort, MpScenario.key))
    ).scalars().all()
    notices = (
        await db.execute(select(MpNotice).order_by(MpNotice.sort, MpNotice.id))
    ).scalars().all()

    providers = await db.scalar(select(func.count()).select_from(MpProvider))
    services = await db.scalar(
        select(func.count()).select_from(MpService).where(MpService.on_shelf.is_(True))
    )
    users = await db.scalar(select(func.count()).select_from(User))

    return HomeOut(
        scenarios=[
            ScenarioOut(key=s.key, name=s.name, tagline=s.tagline, sort=s.sort)
            for s in scenarios
        ],
        notices=[HomeNoticeOut(id=n.id, content=n.content) for n in notices],
        stats=HomeStatsOut(
            providers=int(providers or 0),
            services=int(services or 0),
            users=int(users or 0),
        ),
    )
