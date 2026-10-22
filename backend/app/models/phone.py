"""CCC 电话通路域模型（工作二切片 E，2026-10-22）：隔离状态机 + 适配接口。

外部边界：CCC 号码 / 准入 / 合同 / 话术审核流程未落实——无真实外呼；生产适配器
注册表为空（工单审核后 pending_config 待配置），mock 结果不计真实接通。

三类分立授权（缺一拦截）：本人电话（steward_phone_outreach）、指定联系人
（steward_contact_outreach 框架授权 + 联系人自身授权状态，本人同意≠联系人同意）、
通话录音（steward_call_recording，单独授权）。
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PhoneOrderStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    QUEUED = "queued"
    PENDING_CONFIG = "pending_config"
    IN_PROGRESS = "in_progress"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    WRONG_NUMBER = "wrong_number"
    REFUSED = "refused"
    TRANSFERRED_HUMAN = "transferred_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 未终态（可停止 / 可调度）
PHONE_OPEN_STATUSES = (
    PhoneOrderStatus.PENDING_REVIEW.value,
    PhoneOrderStatus.QUEUED.value,
    PhoneOrderStatus.PENDING_CONFIG.value,
    PhoneOrderStatus.IN_PROGRESS.value,
    PhoneOrderStatus.TRANSFERRED_HUMAN.value,
)
# 可调度（sweep 拾取）
PHONE_DISPATCHABLE = (PhoneOrderStatus.QUEUED.value,)
# 终态
PHONE_TERMINAL_STATUSES = (
    PhoneOrderStatus.WRONG_NUMBER.value,
    PhoneOrderStatus.REFUSED.value,
    PhoneOrderStatus.COMPLETED.value,
    PhoneOrderStatus.FAILED.value,
    PhoneOrderStatus.CANCELLED.value,
)

PHONE_STOP_REASONS = (
    "consent_revoked",
    "contact_refused",
    "instance_closed",
    "script_retired",
    "manual_cancel",
)


class PhoneScriptStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"
    REJECTED = "rejected"


class PhoneContactConsent(str, enum.Enum):
    NONE = "none"
    GRANTED = "granted"
    REFUSED = "refused"
    REVOKED = "revoked"


class PhoneTargetType(str, enum.Enum):
    SELF = "self"
    CONTACT = "contact"


class PhoneScript(TimestampMixin, Base):
    """话术版本：draft → 审核通过 active（同 code 旧版自动 retired）；只有 active 可被引用。"""

    __tablename__ = "phone_scripts"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_phone_scripts_version"),
        CheckConstraint(
            "status IN ('draft', 'active', 'retired', 'rejected')", name="ck_phone_scripts_status"
        ),
        CheckConstraint("target_type IN ('self', 'contact')", name="ck_phone_scripts_target"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)
    requires_recording: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default="draft", nullable=False)
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class PhoneContact(TimestampMixin, Base):
    """指定联系人：身份关系须核验（identity_note 必填）；授权状态独立于本人框架授权。"""

    __tablename__ = "phone_contacts"
    __table_args__ = (
        Index("ix_phone_contacts_user", "user_id", "id"),
        CheckConstraint(
            "consent_status IN ('none', 'granted', 'refused', 'revoked')",
            name="ck_phone_contacts_consent",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    relation: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    identity_note: Mapped[str] = mapped_column(String(200), nullable=False)
    consent_status: Mapped[str] = mapped_column(
        String(10), default="none", server_default="none", nullable=False
    )
    consent_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class PhoneCallOrder(TimestampMixin, Base):
    """电话工单：话术版本 / 对象 / 录音要求随单固化；条件 UPDATE 保证单飞。"""

    __tablename__ = "phone_call_orders"
    __table_args__ = (
        Index("ix_phone_orders_instance", "instance_id", "id"),
        Index("ix_phone_orders_dispatch", "status", "next_retry_at"),
        CheckConstraint(
            "status IN ('pending_review', 'queued', 'pending_config', 'in_progress', 'no_answer', "
            "'busy', 'wrong_number', 'refused', 'transferred_human', 'completed', 'failed', 'cancelled')",
            name="ck_phone_orders_status",
        ),
        CheckConstraint("target_type IN ('self', 'contact')", name="ck_phone_orders_target"),
        CheckConstraint("attempts >= 0", name="ck_phone_orders_attempts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("phone_contacts.id", ondelete="SET NULL"), nullable=True
    )
    script_id: Mapped[int] = mapped_column(ForeignKey("phone_scripts.id", ondelete="RESTRICT"), nullable=False)
    script_code: Mapped[str] = mapped_column(String(32), nullable=False)
    script_version: Mapped[str] = mapped_column(String(16), nullable=False)
    requires_recording: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(
        String(18), default=PhoneOrderStatus.PENDING_REVIEW.value, server_default="pending_review", nullable=False
    )
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(String(500), nullable=True)
    receipt_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 真实回执引用；mock 恒 null
    taken_over_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    taken_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class PhoneCallAttempt(TimestampMixin, Base):
    """尝试记录（只追加）：结果、回执引用与是否模拟（mock 不计真实接通）。"""

    __tablename__ = "phone_call_attempts"
    __table_args__ = (
        Index("ix_phone_attempts_order", "order_id", "id"),
        CheckConstraint(
            "outcome IN ('no_answer', 'busy', 'wrong_number', 'refused', 'needs_human', 'completed', 'blocked')",
            name="ck_phone_attempts_outcome",
        ),
        CheckConstraint("actor IN ('system', 'admin')", name="ck_phone_attempts_actor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("phone_call_orders.id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(8), default="system", server_default="system", nullable=False)
    actor_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    receipt_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
