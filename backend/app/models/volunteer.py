"""杏临千行志愿者报名：公开填报，个人信息仅用于报名联系。"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class VolunteerApplication(TimestampMixin, Base):
    __tablename__ = "volunteer_applications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'contacted', 'enrolled')", name="ck_volunteer_status"
        ),
        CheckConstraint(
            "track IN ('practice', 'publicity', 'assist', 'promotion', 'other')",
            name="ck_volunteer_track",
        ),
        CheckConstraint(
            "gender IS NULL OR gender IN ('male', 'female', 'undisclosed')",
            name="ck_volunteer_gender",
        ),
        CheckConstraint(
            "offline_per_year >= 1 AND offline_per_year <= 99",
            name="ck_volunteer_offline_per_year",
        ),
        CheckConstraint(
            "birth_year IS NULL OR (birth_year >= 1900 AND birth_year <= 2010)",
            name="ck_volunteer_birth_year",
        ),
        Index("ix_volunteer_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    city: Mapped[str] = mapped_column(String(50), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(12), nullable=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(50), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(100), nullable=True)
    track: Mapped[str] = mapped_column(String(10), nullable=False)
    track_note: Mapped[str | None] = mapped_column(String(50), nullable=True)
    offline_per_year: Mapped[int] = mapped_column(Integer, nullable=False)
    can_station: Mapped[bool] = mapped_column(Boolean, nullable=False)
    intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_text: Mapped[str] = mapped_column(String(500), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contact_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
