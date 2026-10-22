"""电话通路域 DTO（工作二切片 E，契约 docs/api.md 第五部分 · 十）。号码掩码，不回显密钥。"""

from pydantic import BaseModel, Field

from app.schemas.common import UTCDateTime

TargetType = str  # self | contact


class ScriptPublishIn(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_-]+$")
    version: str = Field(pattern=r"^v\d+$")
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=5000)
    target_type: TargetType
    requires_recording: bool = False


class ScriptReviewIn(BaseModel):
    approve: bool
    note: str = Field(min_length=1, max_length=200)


class ScriptOut(BaseModel):
    id: int
    code: str
    version: str
    title: str
    content: str
    target_type: str
    requires_recording: bool
    status: str
    review_note: str | None
    created_at: UTCDateTime


class ContactCreateIn(BaseModel):
    user_id: int
    name: str = Field(min_length=1, max_length=50)
    relation: str = Field(min_length=1, max_length=50)
    phone: str = Field(min_length=5, max_length=20)
    identity_note: str = Field(min_length=1, max_length=200)


class ContactConsentIn(BaseModel):
    status: str  # granted | refused | revoked
    source: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=200)


class ContactOut(BaseModel):
    id: int
    user_id: int
    name: str
    relation: str
    phone_masked: str
    identity_note: str
    consent_status: str
    consent_source: str | None
    consent_at: UTCDateTime | None
    created_at: UTCDateTime


class OrderCreateIn(BaseModel):
    instance_id: int
    purpose: str = Field(min_length=1, max_length=32)
    target_type: TargetType
    contact_id: int | None = None
    script_id: int


class OrderReviewIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=200)


class TakeoverIn(BaseModel):
    note: str | None = Field(default=None, max_length=200)


class CompleteIn(BaseModel):
    result: str = Field(min_length=1, max_length=500)


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class AttemptOut(BaseModel):
    id: int
    order_id: int
    outcome: str
    actor: str
    actor_admin_id: int | None
    simulated: bool
    receipt_ref: str | None
    detail: dict | None
    started_at: UTCDateTime
    finished_at: UTCDateTime | None


class OrderOut(BaseModel):
    id: int
    instance_id: int
    user_id: int
    purpose: str
    target_type: str
    contact_id: int | None
    script_code: str
    script_version: str
    requires_recording: bool
    status: str
    stop_reason: str | None
    attempts: int
    next_retry_at: UTCDateTime | None
    result: str | None
    receipt_ref: str | None
    taken_over_by_admin_id: int | None
    taken_over_at: UTCDateTime | None
    created_at: UTCDateTime


class OrderDetailOut(OrderOut):
    attempts_log: list[AttemptOut] = []


class PhoneStatusOut(BaseModel):
    consents: list[dict]
    contacts: list[dict]
    orders: list[dict]
    ccc_ready: bool
    note: str
