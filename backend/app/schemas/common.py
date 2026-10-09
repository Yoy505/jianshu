"""通用 schema 与工具：分页容器、UTC 时间序列化、摘要与手机号掩码。"""

from datetime import datetime, timezone
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, PlainSerializer

T = TypeVar("T")


def as_utc(dt: datetime) -> datetime:
    """SQLite 等驱动可能返回 naive datetime（实际为 UTC），统一补上时区。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _to_utc_iso(dt: datetime) -> str:
    """序列化为 ISO 8601 UTC 字符串，如 2026-09-21T08:30:00Z（契约约定）。"""
    return as_utc(dt).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# when_used="json"：仅 JSON 序列化输出 ISO-Z 字符串；
# python 模式 dump（如构造 ORM 对象）保留 datetime，避免字符串写入 DateTime 列
UTCDateTime = Annotated[datetime, PlainSerializer(_to_utc_iso, when_used="json")]


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


def make_excerpt(content: str, limit: int = 120) -> str:
    """列表项摘要：压缩空白后取前 120 字。"""
    return " ".join((content or "").split())[:limit]


def mask_phone(phone: str | None) -> str | None:
    """手机号掩码：138****0001。"""
    if not phone or len(phone) < 8:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"
