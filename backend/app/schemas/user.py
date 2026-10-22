"""C 端用户 DTO。"""

from pydantic import BaseModel, Field

from app.schemas.common import UTCDateTime


class UserOut(BaseModel):
    id: int
    nickname: str
    avatar: str | None
    phone: str | None  # 掩码输出，如 138****0001
    status: str
    created_at: UTCDateTime
    last_login_at: UTCDateTime | None


class WxLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    phone_code: str | None = Field(default=None, min_length=1, max_length=512)


class SmsSendRequest(BaseModel):
    phone: str


class SmsLoginRequest(BaseModel):
    phone: str
    code: str


class CLoginResponse(BaseModel):
    token: str
    refresh_token: str
    user: UserOut
    is_new: bool
