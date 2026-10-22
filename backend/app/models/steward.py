"""全域健康管家服务履约域模型（工作二，2026-10-22）。

覆盖：服务实例（权益 1:1）、九项激活门禁、履约任务 / 日历、暂停与补时、
服务时间线、履约人员（管家 / 医师 / 替补）。表名前缀 steward_，契约见 docs/api.md 第五部分。

关键不变量由数据库约束保证：
- 一份权益至多一个服务实例 UNIQUE(entitlement_id)；实例号全局唯一；
- 门禁项 UNIQUE(instance_id, check_type)；任务 UNIQUE(instance_id, task_key)——
  重复激活 / 并发生成日历由唯一约束兜底，服务层冲突后读回原记录；
- 时间线只追加，任何状态迁移不删除历史任务（终止时置 cancelled 留痕）。
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StewardInstanceStatus(str, enum.Enum):
    PREPARING = "preparing"
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    CLOSED = "closed"
    TERMINATED = "terminated"


class PrepCheckType(str, enum.Enum):
    IDENTITY_PHONE = "identity_phone"
    ENTITLEMENT_VALID = "entitlement_valid"
    DELIVERY = "delivery"
    DEVICE_PAIRED = "device_paired"
    DEVICE_VENDOR_CONSENT = "device_vendor_consent"
    PLATFORM_CONSENT = "platform_consent"
    FIRST_VALID_DATA = "first_valid_data"
    PHYSICIAN_PLAN = "physician_plan"
    STAFFING_READY = "staffing_ready"


class PrepCheckStatus(str, enum.Enum):
    PENDING = "pending"
    PASSED = "passed"


class PrepCheckSource(str, enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"


class StewardTaskType(str, enum.Enum):
    DAILY = "daily"
    FOLLOW_UP = "follow_up"
    REVIEW_PREP = "review_prep"
    FINAL_REVIEW = "final_review"
    CLOSURE = "closure"


class StewardTaskStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskAssigneeType(str, enum.Enum):
    USER = "user"
    BUTLER = "butler"
    PHYSICIAN = "physician"
    SYSTEM = "system"


class PauseCause(str, enum.Enum):
    PROVIDER_INTERRUPTION = "provider_interruption"
    PHYSICIAN_ADVISED = "physician_advised"
    USER_REQUEST = "user_request"
    OTHER = "other"


class StewardStaffRole(str, enum.Enum):
    BUTLER = "butler"
    PHYSICIAN = "physician"


# 补时规则：医师建议暂停全额补时；服务方核心中断超过 24h 全额补时，不足仅记录
PAUSE_COMPENSATION_MIN_SECONDS = 86400

# 门禁项元数据：check_type -> (标题, 判定来源)；auto 项由服务端实时判定
PREP_CHECK_META: dict[str, tuple[str, PrepCheckSource]] = {
    PrepCheckType.IDENTITY_PHONE.value: ("本人手机号与微信身份核验", PrepCheckSource.AUTO),
    PrepCheckType.ENTITLEMENT_VALID.value: ("权益属于本人且有效", PrepCheckSource.AUTO),
    PrepCheckType.DELIVERY.value: ("收货完成", PrepCheckSource.MANUAL),
    PrepCheckType.DEVICE_PAIRED.value: ("设备配对完成", PrepCheckSource.MANUAL),
    PrepCheckType.DEVICE_VENDOR_CONSENT.value: ("设备厂商数据授权", PrepCheckSource.AUTO),
    PrepCheckType.PLATFORM_CONSENT.value: ("平台服务授权", PrepCheckSource.AUTO),
    PrepCheckType.FIRST_VALID_DATA.value: ("首条本人有效设备数据", PrepCheckSource.MANUAL),
    PrepCheckType.PHYSICIAN_PLAN.value: ("医师初评并签发个人计划", PrepCheckSource.AUTO),
    PrepCheckType.STAFFING_READY.value: ("管家及替补排班就绪", PrepCheckSource.AUTO),
}


class StewardStaff(TimestampMixin, Base):
    """履约人员：管家 / 医师（替补以第二名管家指派表达）；停用不删除，指派留痕。"""

    __tablename__ = "steward_staff"
    __table_args__ = (
        CheckConstraint("role IN ('butler', 'physician')", name="ck_steward_staff_role"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_steward_staff_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    org_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str | None] = mapped_column(String(50), nullable=True)
    license_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active", nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class StewardServiceInstance(TimestampMixin, Base):
    """服务实例：权益激活即懒创建（preparing），九项门禁全过 + 本人确认才转 active。

    服务期 = activated_at 起 service_days×24h + Σ补偿；与权益自然日有效期是两个口径。
    """

    __tablename__ = "steward_service_instances"
    __table_args__ = (
        UniqueConstraint("entitlement_id", name="uq_steward_instances_entitlement"),
        Index("ix_steward_instances_user", "user_id", "id"),
        Index("ix_steward_instances_status", "status", "created_at"),
        Index("ix_steward_instances_butler", "butler_id"),
        CheckConstraint(
            "status IN ('preparing', 'active', 'paused', 'expired', 'closed', 'terminated')",
            name="ck_steward_instances_status",
        ),
        CheckConstraint("service_days >= 1 AND service_days <= 3650", name="ck_steward_instances_days"),
        CheckConstraint("compensated_seconds >= 0", name="ck_steward_instances_compensated"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    entitlement_id: Mapped[int] = mapped_column(
        ForeignKey("mp_entitlements.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    # 权益快照（与 mp_entitlements 一致，便于审计独立成账）
    package_id: Mapped[int] = mapped_column(
        ForeignKey("mp_packages.id", ondelete="RESTRICT"), nullable=False
    )
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("mp_package_versions.id", ondelete="RESTRICT"), nullable=False
    )
    package_name: Mapped[str] = mapped_column(String(100), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    benefits: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    validity_days: Mapped[int] = mapped_column(Integer, nullable=False)
    service_days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12),
        default=StewardInstanceStatus.PREPARING.value,
        server_default="preparing",
        nullable=False,
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    compensated_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    calendar_generated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    plan_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    butler_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=True
    )
    backup_butler_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=True
    )
    physician_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=True
    )
    renewed_from_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="RESTRICT"), nullable=True
    )
    termination_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_report: Mapped[str | None] = mapped_column(Text, nullable=True)


class StewardPrepCheck(TimestampMixin, Base):
    """激活门禁项：auto 项状态为服务端判定快照（每次门禁读取实时重判回写），
    manual 项只能经后台接口带凭据置 passed。"""

    __tablename__ = "steward_prep_checks"
    __table_args__ = (
        UniqueConstraint("instance_id", "check_type", name="uq_steward_prep_checks_type"),
        CheckConstraint("status IN ('pending', 'passed')", name="ck_steward_prep_checks_status"),
        CheckConstraint("source IN ('auto', 'manual')", name="ck_steward_prep_checks_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="CASCADE"), nullable=False
    )
    check_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(6), nullable=False)
    status: Mapped[str] = mapped_column(
        String(8), default=PrepCheckStatus.PENDING.value, server_default="pending", nullable=False
    )
    missing_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evidence: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StewardTask(TimestampMixin, Base):
    """履约任务（日历）：每日事项、随访、复评与结案；结果只追加不删除。"""

    __tablename__ = "steward_tasks"
    __table_args__ = (
        UniqueConstraint("instance_id", "task_key", name="uq_steward_tasks_key"),
        Index("ix_steward_tasks_instance_day", "instance_id", "service_day"),
        Index("ix_steward_tasks_instance_date", "instance_id", "service_date"),
        Index("ix_steward_tasks_user_date", "user_id", "service_date"),
        CheckConstraint(
            "task_type IN ('daily', 'follow_up', 'review_prep', 'final_review', 'closure')",
            name="ck_steward_tasks_type",
        ),
        CheckConstraint("status IN ('pending', 'done', 'cancelled')", name="ck_steward_tasks_status"),
        CheckConstraint(
            "assignee_type IN ('user', 'butler', 'physician', 'system')",
            name="ck_steward_tasks_assignee",
        ),
        CheckConstraint("service_day >= 1", name="ck_steward_tasks_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    task_key: Mapped[str] = mapped_column(String(40), nullable=False)
    service_day: Mapped[int] = mapped_column(Integer, nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    task_type: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    assignee_type: Mapped[str] = mapped_column(String(10), nullable=False)
    butler_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=True
    )
    physician_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=True
    )
    plan_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), default=StewardTaskStatus.PENDING.value, server_default="pending", nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(String(500), nullable=True)
    shifted_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    actual_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backfill_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class StewardPauseRecord(TimestampMixin, Base):
    """暂停与补时：恢复时按规则结算补偿（补偿口径见服务层），平移留痕。"""

    __tablename__ = "steward_pause_records"
    __table_args__ = (
        Index("ix_steward_pauses_instance", "instance_id", "id"),
        CheckConstraint(
            "cause IN ('provider_interruption', 'physician_advised', 'user_request', 'other')",
            name="ck_steward_pauses_cause",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="CASCADE"), nullable=False
    )
    cause: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compensated_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tasks_shifted_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class StewardTimelineEvent(TimestampMixin, Base):
    """服务时间线：只追加。谁（actor）、何时、什么事、依据（detail）。"""

    __tablename__ = "steward_timeline_events"
    __table_args__ = (
        Index("ix_steward_timeline_instance", "instance_id", "id"),
        Index("ix_steward_timeline_user", "user_id", "id"),
        CheckConstraint(
            "actor_type IN ('user', 'admin', 'system')", name="ck_steward_timeline_actor"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(8), default="system", server_default="system", nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_name: Mapped[str] = mapped_column(String(100), default="", server_default="", nullable=False)


class StewardConsentTerms(TimestampMixin, Base):
    """履约授权条款版本（S1）：运营发布新版本即成为该 purpose 唯一 active 当前版本；
    门禁与授权都以当前版本为准，旧版本授权不自动沿用。"""

    __tablename__ = "steward_consent_terms"
    __table_args__ = (
        UniqueConstraint("purpose", "version", name="uq_steward_consent_terms_version"),
        Index("ix_steward_consent_terms_purpose", "purpose", "status", "id"),
        CheckConstraint(
            "purpose IN ('steward_service', 'steward_device_data')",
            name="ck_steward_consent_terms_purpose",
        ),
        CheckConstraint("status IN ('active', 'retired')", name="ck_steward_consent_terms_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(8), default="active", server_default="active", nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


# 试点放行凭据必填八项（S2）：结构化来源与编号，不接受「已完成」式描述
PILOT_RELEASE_EVIDENCE_ITEMS = (
    "contract_attachment",
    "device_supply",
    "org_contract",
    "physician_staffing",
    "care_plan_rules",
    "refund_interruption",
    "duty_ops",
    "outreach_channel",
)


class StewardPilotRelease(TimestampMixin, Base):
    """试点放行（S2）：可审计的激活前置放行记录；无有效记录时激活一律阻断（默认未放行）。"""

    __tablename__ = "steward_pilot_releases"
    __table_args__ = (
        Index("ix_steward_releases_lookup", "package_version_id", "env", "revoked_at"),
        CheckConstraint(
            "env IN ('dev', 'test', 'staging', 'prod')", name="ck_steward_releases_env"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("mp_packages.id", ondelete="RESTRICT"), nullable=False)
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("mp_package_versions.id", ondelete="RESTRICT"), nullable=False
    )
    env: Mapped[str] = mapped_column(String(10), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(50), nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class StewardCarePlan(TimestampMixin, Base):
    """医师签发的个人计划（A1 最小结构；切片 D 扩展为完整版本化签发与适用范围）。
    physician_plan 门禁按「未撤销、未过期、签发医师 = 实例签约医师」自动判定。"""

    __tablename__ = "steward_care_plans"
    __table_args__ = (
        UniqueConstraint("user_id", "version", name="uq_steward_care_plans_user_version"),
        Index("ix_steward_care_plans_user", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="SET NULL"), nullable=True
    )
    physician_id: Mapped[int] = mapped_column(
        ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=False
    )
    org_name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class StewardActivationCredential(TimestampMixin, Base):
    """正式激活凭证（A1）：与激活同事务写入的不可变快照；preparing 草稿不产生凭证。"""

    __tablename__ = "steward_activation_credentials"
    __table_args__ = (
        UniqueConstraint("instance_id", name="uq_steward_activation_credentials_instance"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("steward_service_instances.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gate_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    consent_versions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    plan_version: Mapped[str] = mapped_column(String(32), nullable=False)
    butler_id: Mapped[int] = mapped_column(ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=False)
    backup_butler_id: Mapped[int] = mapped_column(
        ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=False
    )
    physician_id: Mapped[int] = mapped_column(ForeignKey("steward_staff.id", ondelete="RESTRICT"), nullable=False)
    service_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
