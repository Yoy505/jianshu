"""服务履约域 DTO（契约 docs/api.md 第五部分）。响应不直接暴露 ORM，手机号掩码。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UTCDateTime
from app.schemas.commerce import BenefitItem

StewardPurposeType = Literal["steward_service", "steward_device_data"]
StaffRoleType = Literal["butler", "physician"]
PauseCauseType = Literal["provider_interruption", "physician_advised", "user_request", "other"]


# ───────────────────────── 公共片段 ─────────────────────────


class StaffRef(BaseModel):
    id: int
    name: str
    role: str
    phone_masked: str | None


class PrepCheckOut(BaseModel):
    check_type: str
    title: str
    source: str
    status: str
    missing_reason: str | None
    evidence: str | None
    completed_at: UTCDateTime | None


class StewardTaskOut(BaseModel):
    id: int
    service_day: int
    service_date: date
    task_type: str
    title: str
    assignee_type: str
    status: str
    due_at: UTCDateTime | None
    result: str | None
    completed_at: UTCDateTime | None
    shifted_days: int


class CalendarDayOut(BaseModel):
    date: date
    service_day: int | None
    tasks: list[StewardTaskOut]


class PauseOut(BaseModel):
    id: int
    cause: str
    reason: str
    started_at: UTCDateTime
    ended_at: UTCDateTime | None
    duration_seconds: int | None
    compensated_seconds: int | None
    tasks_shifted_days: int | None
    note: str | None


class TimelineOut(BaseModel):
    id: int
    event_type: str
    detail: dict | None
    actor_type: str
    actor_name: str
    created_at: UTCDateTime


class InstanceOut(BaseModel):
    id: int
    instance_no: str
    entitlement_id: int
    package_name: str
    version_no: int
    benefits: list[BenefitItem]
    validity_days: int
    service_days: int
    status: str
    activated_at: UTCDateTime | None
    ends_at: UTCDateTime | None
    original_ends_at: UTCDateTime | None
    compensated_seconds: int
    current_day: int | None
    butler: StaffRef | None
    backup_butler: StaffRef | None
    physician: StaffRef | None
    plan_version: str | None
    renewed_from_instance_id: int | None
    termination_reason: str | None
    terminated_at: UTCDateTime | None
    closed_at: UTCDateTime | None
    created_at: UTCDateTime


class InstanceDetailOut(InstanceOut):
    user_id: int
    user_nickname: str | None = None
    user_phone_masked: str | None = None
    prep_checks: list[PrepCheckOut] = []
    today_tasks: list[StewardTaskOut] = []
    open_pause: PauseOut | None = None
    task_summary: dict | None = None
    pauses: list[PauseOut] = []


# ───────────────────────── C 端请求 ─────────────────────────


class ConsentGrantIn(BaseModel):
    purpose: StewardPurposeType


class TaskCompleteIn(BaseModel):
    result: str | None = Field(default=None, max_length=500)


# ───────────────────────── 后台请求 ─────────────────────────


class PrepPassIn(BaseModel):
    evidence: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=200)


class PrepResetIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class AssignIn(BaseModel):
    butler_id: int | None = None
    backup_butler_id: int | None = None
    physician_id: int | None = None


class PauseIn(BaseModel):
    cause: PauseCauseType
    reason: str = Field(min_length=1, max_length=200)


class ResumeIn(BaseModel):
    note: str | None = Field(default=None, max_length=200)


class CloseIn(BaseModel):
    closure_report: str = Field(min_length=1, max_length=2000)


class TaskResultIn(BaseModel):
    result: str = Field(min_length=1, max_length=500)
    done: bool = True


class StaffCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    role: StaffRoleType
    org_name: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=50)
    license_no: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=500)


class StaffUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    org_name: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=50)
    license_no: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=500)
    status: Literal["active", "inactive"] | None = None


class StaffOut(BaseModel):
    id: int
    name: str
    role: str
    phone_masked: str | None
    org_name: str | None
    title: str | None
    license_no: str | None
    status: str
    note: str | None
    created_at: UTCDateTime
