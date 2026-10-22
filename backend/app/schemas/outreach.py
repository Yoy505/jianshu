"""触达任务域 DTO（工作二切片 B，契约 docs/api.md 第五部分 · 七）。手机号掩码，不暴露回执。"""

from datetime import date

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UTCDateTime

OutreachStatusType = str
OutreachPurposeType = Literal["daily_reminder", "follow_up"]
ContactMethodType = Literal["phone", "chat", "other"]


class IntentOut(BaseModel):
    id: int
    instance_id: int
    user_id: int
    user_nickname: str | None = None
    user_phone_masked: str | None = None
    task_id: int | None
    service_date: date
    purpose: str
    content_version: str
    status: str
    contactable: bool
    contact_note: str | None
    assignee_staff_id: int | None
    assignee_name: str | None = None
    attempts: int
    stop_reason: str | None
    channel_receipt_ref: str | None  # 通道接入前恒 null，字段先行以固定契约形状
    result: str | None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class AttemptOut(BaseModel):
    id: int
    intent_id: int
    action: str
    actor_type: str
    actor_admin_id: int | None
    actor_name: str
    contact_method: str | None
    outcome: str | None
    detail: dict | None
    created_at: UTCDateTime


class IntentDetailOut(IntentOut):
    attempts_log: list[AttemptOut] = []


class RouteManualIn(BaseModel):
    assignee_staff_id: int
    note: str | None = Field(default=None, max_length=200)


class AssignIn(BaseModel):
    assignee_staff_id: int
    note: str | None = Field(default=None, max_length=200)


class ReleaseIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class SubmitIn(BaseModel):
    contact_method: ContactMethodType
    outcome: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=200)


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=200)


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class HandoverIn(BaseModel):
    from_staff_id: int
    to_staff_id: int
    reason: str = Field(min_length=1, max_length=200)


class HandoverOut(BaseModel):
    moved: int
    from_staff_id: int
    to_staff_id: int


class SummaryOut(BaseModel):
    date: date
    total: int
    by_status: dict[str, int]


class SubscriptionStatusOut(BaseModel):
    subscribed: bool
    channel_decided: bool = False  # 通道未决策，如实展示
    status_note: str


class SubscriptionSetIn(BaseModel):
    subscribed: bool
