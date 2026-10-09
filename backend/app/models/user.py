import enum
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    BANNED = "banned"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(50), nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    wx_openid: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    wx_unionid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=UserStatus.ACTIVE.value, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    essays = relationship("Essay", back_populates="user", lazy="raise")
    likes = relationship("EssayLike", back_populates="user", lazy="raise")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} {self.nickname}>"
