"""健康域管理后台 DTO（/admin/mp/*，契约见 docs/api.md 第三部分）。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UTCDateTime, mask_phone
from app.schemas.mp import (
    AppointmentProviderRef,
    AppointmentServiceRef,
    ConsultProviderRef,
    ConsultServiceRef,
    DrugBoardItemOut,
    DrugOut,
    ProductOut,
    ProviderOut,
    ServiceOut,
)


class MpAdminStats(BaseModel):
    appointments: dict[str, int]
    consults_open: int
    providers_total: int
    providers_online: int
    services_total: int
    drugs_total: int
    products_total: int


class AppointmentUserRef(BaseModel):
    id: int
    nickname: str
    phone: str | None  # 掩码展示；线下联络用预约单上的 contact_phone


class AppointmentAdminOut(BaseModel):
    id: int
    user: AppointmentUserRef
    service: AppointmentServiceRef
    provider: AppointmentProviderRef
    appt_date: date
    period: str
    address: str | None
    contact_name: str
    contact_phone: str  # 完整展示：线下上门服务的联络依据
    remark: str | None
    status: str
    created_at: UTCDateTime


class ConsultUserRef(BaseModel):
    id: int
    nickname: str


class ConsultAdminOut(BaseModel):
    id: int
    user: ConsultUserRef
    service: ConsultServiceRef
    provider: ConsultProviderRef
    status: str
    message_count: int
    last_message: str | None
    last_message_at: UTCDateTime | None
    created_at: UTCDateTime


class ConsultMessageAdminOut(BaseModel):
    id: int
    sender: str
    content: str
    created_at: UTCDateTime


class ConsultAdminDetail(ConsultAdminOut):
    messages: list[ConsultMessageAdminOut]


class ProviderUpdate(BaseModel):
    name: str | None = None
    gender: str | None = None
    title: str | None = None
    dept: str | None = None
    org: str | None = None
    years: int | None = Field(default=None, ge=0, le=80)
    skills: list[str] | None = None
    intro: str | None = None
    avatar: str | None = None
    online: bool | None = None


class ServiceUpdate(BaseModel):
    name: str | None = None
    desc: str | None = None
    price: int | None = Field(default=None, ge=0)
    unit: str | None = None
    duration_min: int | None = Field(default=None, ge=0)
    on_shelf: bool | None = None


class ServiceAdminOut(ServiceOut):
    """管理端服务视图：额外暴露 on_shelf（C 端列表恒为 true，不下发）。"""

    on_shelf: bool


class NoticeCreate(BaseModel):
    content: str = Field(min_length=1, max_length=200)
    sort: int = 0


class NoticeOut(BaseModel):
    id: int
    content: str
    sort: int
    created_at: UTCDateTime


class DrugAdminDetail(DrugOut):
    board: list[DrugBoardItemOut]


class DrugLeafletAdminOut(BaseModel):
    """说明书待审元数据；有意不包含原件路径、文件名或下载地址。"""

    id: int
    drug_id: int
    drug_common_name: str
    approval_no: str
    source_name: str
    source_document_id: str
    authorization_ref: str
    identity_check_method: str | None
    document_date: date | None
    document_version: str | None
    source_checked_at: date
    file_size: int
    file_sha256: str
    manifest_sha256: str
    status: Literal["pending", "approved", "published", "withdrawn"]
    created_at: UTCDateTime
    updated_at: UTCDateTime
