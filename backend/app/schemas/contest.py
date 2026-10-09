"""征文活动 DTO。"""

from pydantic import BaseModel

from app.models.contest import ContestStatus
from app.schemas.common import UTCDateTime


class ContestCreate(BaseModel):
    title: str
    description: str = ""
    cover_url: str | None = None
    rules: str = ""
    prizes: str = ""
    start_at: UTCDateTime
    end_at: UTCDateTime


class ContestUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    cover_url: str | None = None
    rules: str | None = None
    prizes: str | None = None
    start_at: UTCDateTime | None = None
    end_at: UTCDateTime | None = None


class ContestStatusUpdate(BaseModel):
    status: ContestStatus


class ContestOut(BaseModel):
    id: int
    title: str
    description: str
    cover_url: str | None
    rules: str
    prizes: str
    status: str
    start_at: UTCDateTime
    end_at: UTCDateTime
    essay_count: int
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ContestCurrentOut(ContestOut):
    days_left: int
    participant_count: int
