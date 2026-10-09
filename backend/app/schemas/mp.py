"""杏临汇小程序 DTO：场景、服务、人员、预约、咨询、药品、档案、商品。"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.common import UTCDateTime


class ScenarioOut(BaseModel):
    key: str
    name: str
    tagline: str
    sort: int


class HomeNoticeOut(BaseModel):
    id: int
    content: str


class HomeStatsOut(BaseModel):
    providers: int
    services: int
    users: int


class HomeOut(BaseModel):
    scenarios: list[ScenarioOut]
    notices: list[HomeNoticeOut]
    stats: HomeStatsOut


class ServiceOut(BaseModel):
    id: int
    scenario: str
    scenario_name: str
    service_type: str
    name: str
    desc: str
    price: int
    unit: str
    duration_min: int
    sales: int
    provider_count: int


class ProviderOut(BaseModel):
    id: int
    name: str
    gender: str
    title: str
    dept: str
    org: str
    years: int
    skills: list[str]
    intro: str
    avatar: str | None
    rating: float
    service_count: int
    online: bool


class ProviderSlotOut(BaseModel):
    date: date
    periods: list[str]


class ProviderReviewOut(BaseModel):
    id: int
    user_name: str
    rating: int
    content: str | None
    created_at: UTCDateTime


class ProviderDetailOut(ProviderOut):
    slots: list[ProviderSlotOut]
    reviews: list[ProviderReviewOut]


class AppointmentServiceRef(BaseModel):
    id: int
    name: str
    service_type: str
    price: int
    unit: str


class AppointmentProviderRef(BaseModel):
    id: int
    name: str
    title: str
    avatar: str | None


class AppointmentCreate(BaseModel):
    service_id: int
    provider_id: int
    date: date
    period: str
    address: str | None = None
    contact_name: str
    contact_phone: str
    remark: str | None = None


class AppointmentOut(BaseModel):
    id: int
    status: str
    date: date
    period: str
    address: str | None
    contact_name: str
    contact_phone: str
    remark: str | None
    service: AppointmentServiceRef
    provider: AppointmentProviderRef
    created_at: UTCDateTime


class AppointmentReviewCreate(BaseModel):
    rating: int
    content: str | None = None


class ConsultServiceRef(BaseModel):
    id: int
    name: str
    service_type: str


class ConsultProviderRef(BaseModel):
    id: int
    name: str
    title: str
    avatar: str | None
    online: bool


class ConsultCreate(BaseModel):
    service_id: int
    provider_id: int
    content: str


class ConsultMessageCreate(BaseModel):
    content: str


class ConsultOut(BaseModel):
    id: int
    status: str
    service: ConsultServiceRef
    provider: ConsultProviderRef
    last_message: str | None
    last_message_at: UTCDateTime | None
    created_at: UTCDateTime


class ConsultMessageOut(BaseModel):
    id: int
    sender: str
    content: str
    created_at: UTCDateTime


class DrugOut(BaseModel):
    id: int
    common_name: str
    approval_no: str | None
    specification: str | None
    license_holder: str | None
    source_name: str | None
    source_url: str | None
    source_checked_at: UTCDateTime | None
    publication_status: Literal["demo", "pending", "published", "offline"]
    form: str
    manufacturer: str
    tags: list[str]
    indication: str
    usage_note: str
    contraindication: str
    science: str
    video_url: str | None
    brand_name: str | None
    favorited: bool = False


class DrugBoardItemOut(BaseModel):
    id: int
    user_name: str
    content: str
    created_at: UTCDateTime


class DrugDetailOut(DrugOut):
    board: list[DrugBoardItemOut] = []
    feedback_mode: Literal["demo", "closed"] = "closed"


class DrugFeedbackCreate(BaseModel):
    content: str


class FavoriteToggleOut(BaseModel):
    favorited: bool


class ProfileSave(BaseModel):
    name: str
    gender: str
    birth: str  # YYYY-MM
    height_cm: int | None = None
    weight_kg: float | None = None
    blood: str | None = None
    chronic_tags: list[str] = []
    allergy: str | None = None
    surgery_history: str | None = None
    smoke: bool = False
    drink: bool = False
    sport_freq: str | None = None


class ProfileOut(BaseModel):
    name: str
    gender: str
    birth: str
    height_cm: int | None
    weight_kg: float | None
    blood: str | None
    chronic_tags: list[str]
    allergy: str | None
    surgery_history: str | None
    smoke: bool
    drink: bool
    sport_freq: str | None


class ProductOut(BaseModel):
    id: int
    category: str
    name: str
    desc: str
    price: int
    status: str
