"""后台审核 / 导出 / 任务 / 审计 DTO。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UTCDateTime


class AuditLogOut(BaseModel):
    id: int
    actor_type: str
    actor_id: int | None
    actor_name: str
    action: str
    resource_type: str
    resource_id: int | None
    detail: dict | None
    ip_address: str | None
    created_at: UTCDateTime


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    score: int | None = Field(default=None, ge=0, le=100)
    comment: str | None = None


class FeatureRequest(BaseModel):
    featured: bool


class ExportRequest(BaseModel):
    contest_id: int | None = None
    status: Literal["submitted", "approved", "rejected", "offline"] | None = None


class ExportAccepted(BaseModel):
    task_id: str


class TaskStatusOut(BaseModel):
    task_id: str
    status: Literal["pending", "success", "failed"]
    result: dict | None = None
