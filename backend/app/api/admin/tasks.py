"""后台 · Celery 任务状态查询。"""

from celery.result import AsyncResult
from fastapi import APIRouter, Depends

from app.core.deps import get_current_admin
from app.models import AdminUser
from app.schemas.admin import TaskStatusOut
from app.workers.celery_app import celery_app

router = APIRouter(prefix="/admin/tasks", tags=["admin-tasks"])


@router.get("/{task_id}", response_model=TaskStatusOut)
async def get_task_status(
    task_id: str,
    admin: AdminUser = Depends(get_current_admin),
) -> TaskStatusOut:
    result = AsyncResult(task_id, app=celery_app)
    if result.state == "SUCCESS":
        payload = result.result if isinstance(result.result, dict) else None
        return TaskStatusOut(task_id=task_id, status="success", result=payload)
    if result.state in ("FAILURE", "REVOKED"):
        return TaskStatusOut(task_id=task_id, status="failed", result=None)
    # PENDING / STARTED / RETRY 等一律视为 pending
    return TaskStatusOut(task_id=task_id, status="pending", result=None)
