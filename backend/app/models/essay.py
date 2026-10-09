import enum

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class EssayStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    OFFLINE = "offline"


class ReviewAction(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    OFFLINE = "offline"


class Essay(TimestampMixin, Base):
    __tablename__ = "essays"
    __table_args__ = (
        Index("ix_essays_status_created_at", "status", "created_at"),
        Index("ix_essays_contest_id_status", "contest_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=EssayStatus.SUBMITTED.value, nullable=False
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    contest = relationship("Contest", back_populates="essays", lazy="raise")
    user = relationship("User", back_populates="essays", lazy="raise")
    reviews = relationship(
        "EssayReview", back_populates="essay", lazy="raise", order_by="EssayReview.id"
    )
    likes = relationship("EssayLike", back_populates="essay", lazy="raise")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Essay {self.id} {self.title} ({self.status})>"


class EssayReview(TimestampMixin, Base):
    __tablename__ = "essay_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    essay_id: Mapped[int] = mapped_column(
        ForeignKey("essays.id", ondelete="CASCADE"), nullable=False
    )
    admin_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    essay = relationship("Essay", back_populates="reviews", lazy="raise")
    admin = relationship("AdminUser", lazy="raise")


class EssayLike(TimestampMixin, Base):
    __tablename__ = "essay_likes"
    __table_args__ = (UniqueConstraint("essay_id", "user_id", name="uq_essay_likes_essay_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    essay_id: Mapped[int] = mapped_column(
        ForeignKey("essays.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    essay = relationship("Essay", back_populates="likes", lazy="raise")
    user = relationship("User", back_populates="likes", lazy="raise")
