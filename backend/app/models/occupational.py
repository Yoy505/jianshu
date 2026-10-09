"""职业健康科普作品征集：独立于文学征文的私有投稿（两级评审，方案 B）。"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class OccupationalSubmission(TimestampMixin, Base):
    __tablename__ = "occupational_submissions"
    __table_args__ = (
        CheckConstraint("work_type IN ('article', 'video')", name="ck_occupational_work_type"),
        CheckConstraint(
            "status IN ('submitted', 'initial_reviewing', 'pending_final', 'accepted', 'rejected')",
            name="ck_occupational_status",
        ),
        Index("ix_occupational_status_created", "status", "created_at"),
        Index("ux_occupational_client_submission_id", "client_submission_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    client_submission_id: Mapped[str] = mapped_column(String(36), nullable=False)
    work_type: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    author_name: Mapped[str] = mapped_column(String(50), nullable=False)
    organization: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="submitted", nullable=False)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_review_contact: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_text: Mapped[str] = mapped_column(String(500), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contact_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stored_filename: Mapped[str] = mapped_column(String(80), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(nullable=True)
    video_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 初审软锁：领取人 + 领取时间，30 分钟超时后可被接管（契约 §七之二）
    locked_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    review_records = relationship(
        "OccupationalReviewRecord",
        back_populates="submission",
        lazy="raise",
        order_by="OccupationalReviewRecord.id",
        cascade="all, delete-orphan",
    )


class OccupationalReviewRecord(TimestampMixin, Base):
    """分级评审结论：每投稿每阶段至多一条（(submission_id, stage) 唯一）。"""

    __tablename__ = "occupational_review_records"
    __table_args__ = (
        CheckConstraint("stage IN ('initial', 'final')", name="ck_occupational_review_stage"),
        CheckConstraint("conclusion IN ('passed', 'rejected')", name="ck_occupational_review_conclusion"),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="ck_occupational_review_score"),
        UniqueConstraint("submission_id", "stage", name="uq_occupational_review_submission_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("occupational_submissions.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(10), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_name: Mapped[str] = mapped_column(String(50), nullable=False)
    conclusion: Mapped[str] = mapped_column(String(10), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    submission = relationship("OccupationalSubmission", back_populates="review_records", lazy="raise")
