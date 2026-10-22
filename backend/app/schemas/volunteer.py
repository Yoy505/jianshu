"""杏临千行志愿者报名 DTO；列表电话打码，详情才给明文。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.volunteer import VolunteerApplication
from app.schemas.common import UTCDateTime, mask_phone

Track = Literal["practice", "publicity", "assist", "promotion", "other"]
AdminStatus = Literal["pending", "contacted", "enrolled"]

TRACKS: tuple[tuple[str, str], ...] = (
    ("practice", "公益实践"),
    ("publicity", "科普宣传"),
    ("assist", "活动协助"),
    ("promotion", "宣传支持"),
    ("other", "其他"),
)
TRACK_KEYS = {key for key, _ in TRACKS}

CONSENT_TEXT = (
    "我同意仅为“杏临千行”志愿者招募的报名与活动联系使用本人姓名、"
    "联系电话及其他填报信息。"
)


class VolunteerApplicationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    phone: str = Field(min_length=11, max_length=20)
    city: str = Field(min_length=1, max_length=50)
    birth_year: int | None = Field(default=None, ge=1900, le=2010)
    occupation: str | None = Field(default=None, max_length=50)
    organization: str | None = Field(default=None, max_length=100)
    track: Track
    track_note: str | None = Field(default=None, max_length=50)
    offline_per_year: int = Field(ge=1, le=99)
    can_station: bool
    intro: str | None = Field(default=None, max_length=500)
    consent: bool


class VolunteerReceipt(BaseModel):
    id: int
    receipt_no: str
    submitted_at: UTCDateTime


class VolunteerListItem(BaseModel):
    id: int
    receipt_no: str
    name: str
    phone_masked: str
    city: str
    track: Track
    can_station: bool
    offline_per_year: int
    status: AdminStatus
    submitted_at: UTCDateTime


class VolunteerDetail(VolunteerListItem):
    birth_year: int | None
    occupation: str | None
    organization: str | None
    track_note: str | None
    phone: str
    intro: str | None
    consent_text: str
    consented_at: UTCDateTime


class VolunteerStatusRequest(BaseModel):
    status: AdminStatus


def volunteer_list_item(row: VolunteerApplication) -> VolunteerListItem:
    return VolunteerListItem(
        id=row.id,
        receipt_no=row.receipt_no,
        name=row.name,
        phone_masked=mask_phone(row.phone),
        city=row.city,
        track=row.track,  # type: ignore[arg-type]
        can_station=row.can_station,
        offline_per_year=row.offline_per_year,
        status=row.status,  # type: ignore[arg-type]
        submitted_at=row.created_at,
    )


def volunteer_detail(row: VolunteerApplication) -> VolunteerDetail:
    return VolunteerDetail(
        **volunteer_list_item(row).model_dump(),
        birth_year=row.birth_year,
        occupation=row.occupation,
        organization=row.organization,
        track_note=row.track_note,
        phone=row.phone,
        intro=row.intro,
        consent_text=row.consent_text,
        consented_at=row.consented_at,
    )
