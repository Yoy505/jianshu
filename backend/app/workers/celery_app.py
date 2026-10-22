"""Celery 应用实例：broker/result 均使用 Redis（db1）。"""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "jianshu",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    beat_schedule={
        # 每日 00:30 汇总前一天的累计值写入 daily_stats
        "snapshot-daily-stats": {
            "task": "app.workers.tasks.snapshot_daily_stats",
            "schedule": crontab(minute=30, hour=0),
        },
        # 工作二 A3：每 5 分钟推进服务实例到期 / 权益作废终止（无人读页面也能停止后续任务）
        "steward-lifecycle-sweep": {
            "task": "app.workers.tasks.steward_lifecycle_sweep",
            "schedule": crontab(minute="*/5"),
        },
        # 工作二切片 B：每 5 分钟生成当日触达意图（幂等）并停止失效意图；无发送调用
        "outreach-sweep": {
            "task": "app.workers.tasks.outreach_sweep",
            "schedule": crontab(minute="*/5"),
        },
        # 工作二切片 C：每 15 分钟批量同步设备数据（无适配器时如实 blocked）
        "device-sync-sweep": {
            "task": "app.workers.tasks.device_sync_sweep",
            "schedule": crontab(minute="*/15"),
        },
        # 工作二切片 D：每 15 分钟工单超时升级（标记留痕，不自动外呼）
        "professional-sweep": {
            "task": "app.workers.tasks.professional_sweep",
            "schedule": crontab(minute="*/15"),
        },
        # 工作二切片 E：每 5 分钟调度电话工单（单飞保护；真实外呼关闭）
        "phone-call-sweep": {
            "task": "app.workers.tasks.phone_call_sweep",
            "schedule": crontab(minute="*/5"),
        },
    },
)
