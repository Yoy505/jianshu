"""统计服务：后台仪表盘与每日快照。"""

import datetime
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contest, DailyStat, Essay, EssayLike, User

TREND_DAYS = 14
STATUS_DIST_ORDER = ("approved", "submitted", "rejected", "offline")


async def dashboard_stats(db: AsyncSession) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date()

    users_total = await db.scalar(select(func.count()).select_from(User))
    essays_total = await db.scalar(select(func.count()).select_from(Essay))
    contests_total = await db.scalar(select(func.count()).select_from(Contest))
    pending_review = await db.scalar(
        select(func.count()).select_from(Essay).where(Essay.status == "submitted")
    )
    likes_total = await db.scalar(select(func.count()).select_from(EssayLike))

    # 近 14 天投稿/过审趋势（按 UTC 自然日分组，空日期补零）
    since = datetime.datetime.combine(
        today - timedelta(days=TREND_DAYS - 1), datetime.time.min, tzinfo=datetime.timezone.utc
    )
    date_expr = func.date(Essay.created_at)
    rows = (
        await db.execute(
            select(date_expr, Essay.status, func.count())
            .where(Essay.created_at >= since)
            .group_by(date_expr, Essay.status)
        )
    ).all()
    counter: dict[tuple[str, str], int] = {}
    for day, status, count in rows:
        counter[(str(day), status)] = int(count)

    essay_trend = []
    for offset in range(TREND_DAYS - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        essay_trend.append(
            {
                "date": day,
                "submitted": counter.get((day, "submitted"), 0),
                "approved": counter.get((day, "approved"), 0),
            }
        )

    dist_rows = (
        await db.execute(select(Essay.status, func.count()).group_by(Essay.status))
    ).all()
    dist_map = {status: int(count) for status, count in dist_rows}
    status_dist = [
        {"status": status, "count": dist_map.get(status, 0)} for status in STATUS_DIST_ORDER
    ]

    return {
        "totals": {
            "users": int(users_total or 0),
            "essays": int(essays_total or 0),
            "contests": int(contests_total or 0),
            "pending_review": int(pending_review or 0),
            "likes": int(likes_total or 0),
        },
        "essay_trend": essay_trend,
        "status_dist": status_dist,
    }


async def snapshot_daily(db: AsyncSession, for_date: datetime.date) -> DailyStat:
    """将当日累计值写入/更新 daily_stats（Celery Beat 每日 00:30 调用）。"""
    users_total = await db.scalar(select(func.count()).select_from(User))
    essays_total = await db.scalar(select(func.count()).select_from(Essay))
    likes_total = await db.scalar(select(func.count()).select_from(EssayLike))

    stat = (
        await db.execute(select(DailyStat).where(DailyStat.date == for_date))
    ).scalar_one_or_none()
    if stat is None:
        stat = DailyStat(date=for_date)
        db.add(stat)
    stat.users_total = int(users_total or 0)
    stat.essays_total = int(essays_total or 0)
    stat.likes_total = int(likes_total or 0)
    await db.commit()
    await db.refresh(stat)
    return stat
