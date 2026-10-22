"""Celery 异步任务：短信发送、征文导出 xlsx、每日统计快照。

注意：任务为同步函数，内部用 asyncio.run 包裹异步查询，并为每次执行
创建独立的 async engine/session，任务结束后 dispose，避免跨事件循环复用连接。
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models import Contest, DailyStat, Essay, User
from app.schemas.common import as_utc
from app.services.export_storage import private_export_dir
from app.services.stats_service import snapshot_daily
from app.workers.celery_app import celery_app

logger = logging.getLogger("app.workers")


@celery_app.task(name="app.workers.tasks.send_sms")
def send_sms(phone: str, code: str) -> dict:
    """发送短信验证码。

    开发/测试环境仅打印日志（含验证码，便于联调）。其他环境在接入真实短信
    服务并验证回执前一律失败，不得报告发送成功。
    """
    if settings.APP_ENV not in {"dev", "test"}:
        logger.error("短信通道尚未接入，验证码任务失败")
        raise RuntimeError("短信通道尚未接入，验证码未发送")

    logger.warning("【短信】开发/测试环境模拟发送：%s -> 验证码 %s", phone, code)
    return {"phone": phone, "sent": True}


async def _fetch_export_rows(contest_id: int | None, status: str | None) -> list[tuple]:
    """独立事件循环中查询导出数据，返回纯 Python 元组。"""
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            stmt = (
                select(Essay, User.nickname, Contest.title)
                .join(User, Essay.user_id == User.id)
                .join(Contest, Essay.contest_id == Contest.id)
                .order_by(Essay.created_at.desc())
            )
            if contest_id is not None:
                stmt = stmt.where(Essay.contest_id == contest_id)
            if status:
                stmt = stmt.where(Essay.status == status)
            result = await session.execute(stmt)
            return [
                (
                    essay.id,
                    essay.title,
                    "匿名" if essay.anonymous else nickname,
                    contest_title,
                    essay.status,
                    essay.score,
                    essay.like_count,
                    essay.view_count,
                    as_utc(essay.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                )
                for essay, nickname, contest_title in result.all()
            ]
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.tasks.export_essays_xlsx")
def export_essays_xlsx(contest_id: int | None = None, status: str | None = None) -> dict:
    """将征文作品写入私有目录，返回需后台鉴权的下载路径。"""
    rows = asyncio.run(_fetch_export_rows(contest_id, status))

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "征文作品"
    sheet.append(["ID", "标题", "作者", "活动", "状态", "得分", "点赞", "浏览", "投稿时间"])
    for row in rows:
        sheet.append(list(row))

    exports_dir = private_export_dir()
    exports_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    filename = f"essays_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex}.xlsx"
    file_path = exports_dir / filename
    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            workbook.save(output)
    except Exception:
        file_path.unlink(missing_ok=True)
        raise
    return {"url": f"/admin/essays/exports/{filename}"}


async def _run_snapshot(for_date) -> dict:
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            stat: DailyStat = await snapshot_daily(session, for_date)
            return {
                "date": stat.date.isoformat(),
                "users_total": stat.users_total,
                "essays_total": stat.essays_total,
                "likes_total": stat.likes_total,
            }
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.tasks.snapshot_daily_stats")
def snapshot_daily_stats() -> dict:
    """每日 00:30 执行：汇总刚结束一天的累计用户/投稿/点赞数。"""
    for_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    return asyncio.run(_run_snapshot(for_date))


async def _run_steward_sweep() -> dict:
    from app.services import steward_service

    engine = create_async_engine(settings.DATABASE_URL)
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            return await steward_service.lifecycle_sweep(session)
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.tasks.steward_lifecycle_sweep")
def steward_lifecycle_sweep() -> dict:
    """每 5 分钟（beat）：批量推进服务实例到期与权益作废终止（A3）。
    条件 UPDATE 幂等；与读路径懒迁移共用同一组迁移函数与审计 action。"""
    return asyncio.run(_run_steward_sweep())


async def _run_outreach_sweep() -> dict:
    from app.services import outreach_service

    engine = create_async_engine(settings.DATABASE_URL)
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            return await outreach_service.outreach_sweep(session)
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.tasks.outreach_sweep")
def outreach_sweep() -> dict:
    """每 5 分钟（beat，切片 B）：生成当日触达意图（幂等）并停止失效意图。
    无任何发送调用；通道未决策期间意图保持待通道配置。"""
    return asyncio.run(_run_outreach_sweep())


async def _run_device_sync() -> dict:
    from app.services import device_service

    engine = create_async_engine(settings.DATABASE_URL)
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            return await device_service.sync_all_active(session)
    finally:
        await engine.dispose()


async def _run_professional_sweep() -> dict:
    from app.services import professional_service

    engine = create_async_engine(settings.DATABASE_URL)
    try:
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
            return await professional_service.professional_sweep(session)
    finally:
        await engine.dispose()


@celery_app.task(name="app.workers.tasks.professional_sweep")
def professional_sweep_task() -> dict:
    """每 15 分钟（beat，切片 D）：工单超时升级（标记 + 事件 + 审计，不自动外呼）。"""
    return asyncio.run(_run_professional_sweep())


@celery_app.task(name="app.workers.tasks.device_sync_sweep")
def device_sync_sweep() -> dict:
    """每 15 分钟（beat，切片 C）：批量同步 active 绑定。适配器未注册时逐条
    记录 blocked「厂商适配待配置」，不伪造数据。"""
    return asyncio.run(_run_device_sync())
