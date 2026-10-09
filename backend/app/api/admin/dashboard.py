"""后台仪表盘统计。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db
from app.models import AdminUser
from app.services.stats_service import dashboard_stats

router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])


@router.get("/stats")
async def stats(
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> dict:
    return await dashboard_stats(db)
