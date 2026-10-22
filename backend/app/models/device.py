"""设备云数据域模型（工作二切片 C，2026-10-22）：标准数据域，厂商无关。

外部边界：设备型号与厂商未书面确定——本域只定义标准结构、质量策略与适配接口；
无真实厂商 API 调用，生产环境适配器注册表为空（同步返回待配置并留痕，不伪造数据）。

关键不变量：
- 同一设备同一时刻至多一个 active 绑定（partial unique：vendor+sn 且未解绑）——
  跨用户归属冲突由数据库拒绝；
- 数据点 append-only：`UNIQUE(binding_id, dedup_key, revision)` 区分重复与合法修订，
  趋势取每键最大 revision 且仅 valid/reviewed；
- 错人数据拒绝入库（同步日志计数留痕），不落他人健康数据。
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DeviceBindingStatus(str, enum.Enum):
    ACTIVE = "active"
    UNBOUND = "unbound"


class DeviceCursorStatus(str, enum.Enum):
    IDLE = "idle"
    BLOCKED = "blocked"


class DeviceQuality(str, enum.Enum):
    VALID = "valid"
    INVALID_VALUE = "invalid_value"
    LATE = "late"
    REVIEWED = "reviewed"


class DeviceSyncStatus(str, enum.Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


# 指标合理范围（质量策略；生理合理性边界，非医学阈值）
DEVICE_METRIC_RANGES: dict[str, tuple[float, float, str]] = {
    "sleep_duration": (0, 1440, "min"),
    "sleep_score": (0, 100, "score"),
    "heart_rate": (20, 250, "bpm"),
    "steps": (0, 200000, "count"),
}

# 趋势与规则可用质量
TREND_QUALITIES = (DeviceQuality.VALID.value, DeviceQuality.REVIEWED.value)


class DeviceVendor(TimestampMixin, Base):
    """厂商注册表：注册≠接通；真实适配器上线前同步一律待配置。"""

    __tablename__ = "device_vendors"
    __table_args__ = (
        UniqueConstraint("code", name="uq_device_vendors_code"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_device_vendors_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active", nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class DeviceBinding(TimestampMixin, Base):
    """用户与设备绑定：换绑=解绑后新建行；解绑不删历史（数据归属可追溯）。"""

    __tablename__ = "device_bindings"
    __table_args__ = (
        UniqueConstraint(
            "vendor_id", "device_sn", "active_flag",
            name="uq_device_bindings_active_device",
        ),
        Index("ix_device_bindings_user", "user_id", "status", "id"),
        CheckConstraint("status IN ('active', 'unbound')", name="ck_device_bindings_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("device_vendors.id", ondelete="RESTRICT"), nullable=False)
    device_model: Mapped[str] = mapped_column(String(50), nullable=False)
    device_sn: Mapped[str] = mapped_column(String(64), nullable=False)
    # active 行恒 0、解绑置自身 id：实现「同设备至多一个 active」的部分唯一索引语义
    active_flag: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), default=DeviceBindingStatus.ACTIVE.value, server_default="active", nullable=False
    )
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unbind_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class DeviceSyncCursor(TimestampMixin, Base):
    """按绑定持久化的增量游标：断点恢复、失败计数与 blocked 状态。"""

    __tablename__ = "device_sync_cursors"
    __table_args__ = (
        UniqueConstraint("binding_id", name="uq_device_sync_cursors_binding"),
        CheckConstraint("status IN ('idle', 'blocked')", name="ck_device_sync_cursors_status"),
        CheckConstraint("failure_count >= 0", name="ck_device_sync_cursors_failures"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    binding_id: Mapped[int] = mapped_column(
        ForeignKey("device_bindings.id", ondelete="CASCADE"), nullable=False
    )
    cursor: Mapped[str] = mapped_column(String(128), default="", server_default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), default=DeviceCursorStatus.IDLE.value, server_default="idle", nullable=False
    )
    failure_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DeviceMetric(TimestampMixin, Base):
    """数据点（append-only）：去重键 + revision 区分重复与合法修订；质量标记控制趋势可见性。"""

    __tablename__ = "device_metrics"
    __table_args__ = (
        UniqueConstraint("binding_id", "dedup_key", "revision", name="uq_device_metrics_rev"),
        Index("ix_device_metrics_binding_date", "binding_id", "business_date"),
        Index("ix_device_metrics_user_metric", "user_id", "metric_type", "business_date"),
        CheckConstraint(
            "quality IN ('valid', 'invalid_value', 'late', 'reviewed')",
            name="ck_device_metrics_quality",
        ),
        CheckConstraint("revision >= 1", name="ck_device_metrics_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    binding_id: Mapped[int] = mapped_column(
        ForeignKey("device_bindings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, default=480, server_default="480", nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    vendor_record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    quality: Mapped[str] = mapped_column(
        String(16), default=DeviceQuality.VALID.value, server_default="valid", nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    review_note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class DeviceSyncLog(TimestampMixin, Base):
    """同步日志（每次执行一条）：计数、错误与状态——后台失败列表数据源。"""

    __tablename__ = "device_sync_logs"
    __table_args__ = (
        Index("ix_device_sync_logs_binding", "binding_id", "id"),
        Index("ix_device_sync_logs_status", "status", "id"),
        CheckConstraint(
            "status IN ('success', 'partial', 'failed', 'blocked')",
            name="ck_device_sync_logs_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    binding_id: Mapped[int] = mapped_column(
        ForeignKey("device_bindings.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    pulled: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    stored: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    duplicates: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    revisions: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    invalid: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    late: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    wrong_user: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
