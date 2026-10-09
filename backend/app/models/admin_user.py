import enum

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AdminRole(str, enum.Enum):
    ADMIN = "admin"
    EDITOR = "editor"


class AdminUser(TimestampMixin, Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default=AdminRole.ADMIN.value, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AdminUser {self.username} ({self.role})>"
