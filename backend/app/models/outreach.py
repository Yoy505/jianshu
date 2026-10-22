"""触达任务域模型（工作二切片 B，2026-10-22）：渠道无关的触达意图、尝试记录与退订状态。

通道边界（最高优先级）：本域不含任何发送能力——无通道凭据、无发送 / 送达 / 阅读回执；
通道未决策期间意图初始状态恒为 pending_channel（准确的「待通道配置」），禁止伪造
「已提交通道 / 已发送」。通道选定后另开接入切片再增加真实回执引用字段。

身份绑定：意图直接以 (instance_id, user_id) 关联本人与权益实例，禁止凭昵称、头像、
手机号相似性合并触达对象。

关键不变量由数据库约束保证：
- 业务幂等键 UNIQUE(instance_id, purpose, service_date)——重复调度 / 多 worker 并发
  只建一份意图，冲突读回原记录；
- 尝试记录只追加，任何流转 / 停止 / 交接都留痕，不删除历史。
"""

import enum
from datetime import date

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class OutreachIntentStatus(str, enum.Enum):
    PENDING_CHANNEL = "pending_channel"
    PENDING_MANUAL = "pending_manual"
    PENDING_CONFIRM = "pending_confirm"
    RESOLVED = "resolved"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OutreachPurpose(str, enum.Enum):
    DAILY_REMINDER = "daily_reminder"
    FOLLOW_UP = "follow_up"


# 未终态（可继续流转 / 可被停止）
OUTREACH_OPEN_STATUSES = (
    OutreachIntentStatus.PENDING_CHANNEL.value,
    OutreachIntentStatus.PENDING_MANUAL.value,
    OutreachIntentStatus.PENDING_CONFIRM.value,
)


class OutreachIntent(TimestampMixin, Base):
    """触达意图：一个实例一个服务日一个用途至多一条；责任管家与结果随流转更新。"""

    __tablename__ = "outreach_intents"
    __table_args__ = (
        UniqueConstraint("instance_id", "purpose", "service_date", name="uq_outreach_intents_key"),
        Index("ix_outreach_intents_status_date", "status", "service_date"),
        Index("ix_outreach_intents_assignee", "assignee_staff_id", "status"),
        Index("ix_outreach_intents_user", "user_id", "id"),
        CheckConstraint(
            "status IN ('pending_channel', 'pending_manual', 'pending_confirm', "
            "'resolved', 'failed', 'cancelled')",
            name="ck_outreach_intents_status",
        ),
        CheckConstraint("purpose IN ('daily_reminder', 'follow_up')", name="ck_outreach_intents_purpose"),
        CheckConstraint(
            "stop_reason IN ('instance_expired', 'instance_terminated', 'service_paused', "
            "'unsubscribed', 'consent_revoked', 'manual_cancel') OR stop_reason IS NULL",
            name="ck_outreach_intents_stop_reason",
        ),
        CheckConstraint("attempts >= 0", name="ck_outreach_intents_attempts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_tasks.id", ondelete="SET NULL"), nullable=True
    )
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    content_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=OutreachIntentStatus.PENDING_CHANNEL.value,
        server_default="pending_channel", nullable=False,
    )
    contactable: Mapped[bool] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    contact_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    assignee_staff_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel_receipt_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result: Mapped[str | None] = mapped_column(String(500), nullable=True)


class OutreachAttempt(TimestampMixin, Base):
    """尝试记录（只追加）：生成、转人工、领取、退回、提交、确认、驳回、取消、停止、交接。"""

    __tablename__ = "outreach_attempts"
    __table_args__ = (
        Index("ix_outreach_attempts_intent", "intent_id", "id"),
        CheckConstraint(
            "action IN ('generated', 'routed_manual', 'assigned', 'released', 'submitted', "
            "'confirmed', 'rejected', 'cancelled', 'stopped', 'reassigned')",
            name="ck_outreach_attempts_action",
        ),
        CheckConstraint(
            "contact_method IN ('phone', 'chat', 'other') OR contact_method IS NULL",
            name="ck_outreach_attempts_contact_method",
        ),
        CheckConstraint("actor_type IN ('admin', 'system')", name="ck_outreach_attempts_actor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("outreach_intents.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(8), default="system", server_default="system", nullable=False)
    actor_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    actor_name: Mapped[str] = mapped_column(String(100), default="system", server_default="", nullable=False)
    contact_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(500), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OutreachSubscription(TimestampMixin, Base):
    """本人退订状态（user 级，默认订阅）：退订立即停止后续触达，重新订阅后按后续服务日恢复。"""

    __tablename__ = "outreach_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_outreach_subscriptions_user"),
        Index("ix_outreach_subscriptions_user", "user_id", "subscribed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subscribed: Mapped[bool] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    updated_by: Mapped[str] = mapped_column(String(16), default="user", server_default="user", nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
