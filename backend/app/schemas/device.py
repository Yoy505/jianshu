"""设备云数据域 DTO（工作二切片 C，契约 docs/api.md 第五部分 · 八）。手机号掩码。"""

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.common import UTCDateTime


class VendorCreateIn(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=200)


class VendorOut(BaseModel):
    id: int
    code: str
    name: str
    status: str
    note: str | None
    created_at: UTCDateTime


class BindingCreateIn(BaseModel):
    user_id: int
    vendor_id: int
    device_model: str = Field(min_length=1, max_length=50)
    device_sn: str = Field(min_length=1, max_length=64)


class UnbindIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class CursorResetIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    back: bool = False  # true=游标回退重拉补传窗口


class MetricReviewIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class BindingOut(BaseModel):
    id: int
    user_id: int
    user_nickname: str | None = None
    user_phone_masked: str | None = None
    vendor_id: int
    vendor_name: str | None = None
    device_model: str
    device_sn: str
    status: str
    bound_at: UTCDateTime
    unbound_at: UTCDateTime | None
    unbind_reason: str | None
    cursor: dict | None = None


class SyncLogOut(BaseModel):
    id: int
    binding_id: int
    status: str
    pulled: int
    stored: int
    duplicates: int
    revisions: int
    invalid: int
    late: int
    wrong_user: int
    detail: str | None
    started_at: UTCDateTime
    finished_at: UTCDateTime | None


class SyncRunOut(BaseModel):
    status: str
    detail: str
    pulled: int = 0
    stored: int = 0
    duplicates: int = 0
    revisions: int = 0
    invalid: int = 0
    late: int = 0
    wrong_user: int = 0


class MetricOut(BaseModel):
    id: int
    binding_id: int
    user_id: int
    metric_type: str
    value: float
    unit: str
    business_date: date
    tz_offset_minutes: int
    vendor_record_id: str
    vendor_updated_at: UTCDateTime
    received_at: UTCDateTime
    quality: str
    revision: int
    review_note: str | None


class CursorOut(BaseModel):
    cursor: str
    status: str
    failure_count: int
    last_success_at: UTCDateTime | None
    last_attempt_at: UTCDateTime | None
    last_error: str | None


class TrendOut(BaseModel):
    dates: list[str]
    series: list[float]
    gaps: list[str]
    last_synced_at: UTCDateTime | None
    quality_note: str


class MyDeviceOut(BaseModel):
    binding: BindingOut | None
    consent_granted: bool
    consent_outdated: bool
    sync_note: str
