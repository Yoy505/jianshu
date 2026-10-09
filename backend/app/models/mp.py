"""杏临汇小程序（健康服务域）模型：场景、服务、人员、预约、咨询、药品、档案、商城。

表名统一前缀 mp_，主键 int（场景表按契约以 key 作 String 主键）。
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class MpServiceType(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    THIRD = "third"


class MpAppointmentStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MpConsultStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


# 预约时段（契约：上午/下午/晚间）
MP_PERIODS = ("上午", "下午", "晚间")
# 药品分类标签（契约：otc/rx/essential/insurance 的子集）
MP_DRUG_TAGS = ("otc", "rx", "essential", "insurance")
MP_DRUG_PUBLICATION_STATUSES = ("demo", "pending", "published", "offline")


class MpScenario(TimestampMixin, Base):
    """九大健康场景（key 即业务标识）。"""

    __tablename__ = "mp_scenarios"

    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    tagline: Mapped[str] = mapped_column(String(100), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MpNotice(TimestampMixin, Base):
    """首页公告。"""

    __tablename__ = "mp_notices"
    __table_args__ = (Index("ix_mp_notices_sort", "sort"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(String(200), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MpService(TimestampMixin, Base):
    """健康服务项（线上 / 线下上门 / 三方机构）。"""

    __tablename__ = "mp_services"
    __table_args__ = (Index("ix_mp_services_scenario", "scenario_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_key: Mapped[str] = mapped_column(
        ForeignKey("mp_scenarios.key", ondelete="CASCADE"), nullable=False
    )
    service_type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    desc: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)  # 单位：分
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    sales: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    on_shelf: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MpProvider(TimestampMixin, Base):
    """专业人员（医师 / 药师 / 护师 / 康复治疗师等）。"""

    __tablename__ = "mp_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    dept: Mapped[str] = mapped_column(String(50), nullable=False)
    org: Mapped[str] = mapped_column(String(100), nullable=False)
    years: Mapped[int] = mapped_column(Integer, nullable=False)
    skills: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    intro: Mapped[str] = mapped_column(Text, nullable=False)
    avatar: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rating: Mapped[float] = mapped_column(Numeric(2, 1), default=4.9, nullable=False)
    service_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class MpProviderService(TimestampMixin, Base):
    """人员 ↔ 服务 多对多挂接。"""

    __tablename__ = "mp_provider_services"
    __table_args__ = (
        UniqueConstraint("provider_id", "service_id", name="uq_mp_provider_services_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("mp_providers.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("mp_services.id", ondelete="CASCADE"), nullable=False
    )


class MpAppointment(TimestampMixin, Base):
    """预约单（五步闭环·第四步）。

    并发防重：同一用户同日同时段最多一条进行中（pending/confirmed）预约。
    - PostgreSQL：迁移 a1c4f7e2b9d8 建部分唯一索引 uq_mp_appointments_active_slot；
    - SQLite（测试，经 create_all 建表）：下方 DDL 事件建等价 BEFORE 触发器。
    两条路径都把违反约束的写入映射为 IntegrityError，由服务层转为 409。
    """

    __tablename__ = "mp_appointments"
    __table_args__ = (Index("ix_mp_appointments_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("mp_services.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("mp_providers.id", ondelete="CASCADE"), nullable=False
    )
    appt_date: Mapped[date] = mapped_column(Date, nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(50), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=MpAppointmentStatus.PENDING.value, nullable=False
    )
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True)


class MpReview(TimestampMixin, Base):
    """预约评价（一约一评）。"""

    __tablename__ = "mp_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("mp_appointments.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("mp_providers.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5
    content: Mapped[str | None] = mapped_column(Text, nullable=True)


class MpConsult(TimestampMixin, Base):
    """咚咚在线咨询会话。"""

    __tablename__ = "mp_consults"
    __table_args__ = (Index("ix_mp_consults_user", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("mp_services.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("mp_providers.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default=MpConsultStatus.OPEN.value, nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(nullable=True)


class MpConsultMessage(TimestampMixin, Base):
    """咨询消息（user / provider / system）。"""

    __tablename__ = "mp_consult_messages"
    __table_args__ = (Index("ix_mp_consult_messages_consult_id", "consult_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    consult_id: Mapped[int] = mapped_column(
        ForeignKey("mp_consults.id", ondelete="CASCADE"), nullable=False
    )
    sender: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class MpDrug(TimestampMixin, Base):
    """药品数字展厅条目。"""

    __tablename__ = "mp_drugs"
    __table_args__ = (
        Index("ix_mp_drugs_initial", "initial"),
        UniqueConstraint("approval_no", name="uq_mp_drugs_approval_no"),
        CheckConstraint(
            "publication_status IN ('demo', 'pending', 'published', 'offline')",
            name="ck_mp_drugs_publication_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    common_name: Mapped[str] = mapped_column(String(100), nullable=False)
    brand_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approval_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    specification: Mapped[str | None] = mapped_column(String(100), nullable=True)
    license_holder: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publication_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    form: Mapped[str] = mapped_column(String(50), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    indication: Mapped[str] = mapped_column(Text, nullable=False)
    usage_note: Mapped[str] = mapped_column(Text, nullable=False)
    contraindication: Mapped[str] = mapped_column(Text, nullable=False)
    science: Mapped[str] = mapped_column(Text, nullable=False)
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    initial: Mapped[str] = mapped_column(String(1), nullable=False)  # 拼音首字母 A-Z


class MpDrugLeaflet(TimestampMixin, Base):
    """私有说明书原件版本。导入仅暂存，不改变药品的公开状态。"""

    __tablename__ = "mp_drug_leaflets"
    __table_args__ = (
        UniqueConstraint("drug_id", "file_sha256", name="uq_mp_drug_leaflets_drug_hash"),
        Index("ix_mp_drug_leaflets_drug_status", "drug_id", "status"),
        Index("ix_mp_drug_leaflets_approval_no", "approval_no"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'published', 'withdrawn')",
            name="ck_mp_drug_leaflets_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    drug_id: Mapped[int] = mapped_column(
        ForeignKey("mp_drugs.id", ondelete="RESTRICT"), nullable=False
    )
    approval_no: Mapped[str] = mapped_column(String(50), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_document_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    authorization_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    document_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_checked_at: Mapped[date] = mapped_column(Date, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(68), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # 仅记录机械正文匹配方式；不表示医学审核或转载授权通过。
    identity_check_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )


class MpDrugLeafletIntake(TimestampMixin, Base):
    """未完成药品身份与版本核验的私有 PDF；不关联公开药品。"""

    __tablename__ = "mp_drug_leaflet_intake"
    __table_args__ = (
        UniqueConstraint(
            "source_namespace", "source_record_id", "file_sha256",
            name="uq_mp_drug_leaflet_intake_source_hash",
        ),
        Index("ix_mp_drug_leaflet_intake_review", "review_status", "id"),
        Index("ix_mp_drug_leaflet_intake_claimed_approval", "claimed_approval_no"),
        CheckConstraint(
            "review_status IN ('pending', 'verified', 'rejected')",
            name="ck_mp_drug_leaflet_intake_review",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_acceptance_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    claimed_approval_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    version_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_checked_at: Mapped[date] = mapped_column(Date, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(68), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 暂存不构成公开转载许可；只有后续人工流程才可填写使用依据。
    distribution_authorization_ref: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    review_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )


class MpDrugRegulatorySource(TimestampMixin, Base):
    """离线核验的监管目录来源条目；不代表药品已公开或正在销售。"""

    __tablename__ = "mp_drug_regulatory_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_namespace", "source_record_id",
            name="uq_mp_drug_regulatory_sources_source_key",
        ),
        Index("ix_mp_drug_regulatory_sources_identifier", "registration_identifier"),
        Index("ix_mp_drug_regulatory_sources_class_origin", "drug_class", "origin"),
        CheckConstraint(
            "drug_class IN ('chemical', 'tcm', 'biologic')",
            name="ck_mp_drug_regulatory_sources_class",
        ),
        CheckConstraint(
            "origin IN ('domestic', 'imported')",
            name="ck_mp_drug_regulatory_sources_origin",
        ),
        CheckConstraint(
            "regulatory_status IN ('approved_current', 'revoked', 'expired', 'suspended', 'unknown')",
            name="ck_mp_drug_regulatory_sources_status",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'accepted', 'rejected')",
            name="ck_mp_drug_regulatory_sources_review",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(200), nullable=False)
    registration_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    drug_class: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    regulatory_status: Mapped[str] = mapped_column(String(24), nullable=False)
    source_status_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    regulatory_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stored_document_key: Mapped[str] = mapped_column(String(69), nullable=False)
    source_checked_at: Mapped[date] = mapped_column(Date, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_drug_id: Mapped[int | None] = mapped_column(
        ForeignKey("mp_drugs.id", ondelete="SET NULL"), nullable=True
    )
    review_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )


class MpDrugRegulatoryObservation(TimestampMixin, Base):
    """来源条目出现于某份私有原件的证据；保留历史快照引用。"""

    __tablename__ = "mp_drug_regulatory_observations"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "source_document_sha256", "source_url", "fact_sha256",
            name="uq_mp_drug_regulatory_observations_evidence",
        ),
        Index("ix_mp_drug_regulatory_observations_hash", "source_document_sha256"),
        CheckConstraint(
            "review_status IN ('pending', 'accepted', 'rejected')",
            name="ck_mp_drug_regulatory_observations_review",
        ),
        CheckConstraint(
            "kind IN ('matching', 'conflict')",
            name="ck_mp_drug_regulatory_observations_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("mp_drug_regulatory_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stored_document_key: Mapped[str] = mapped_column(String(69), nullable=False)
    source_document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    fact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reported_facts: Mapped[dict] = mapped_column(JSON, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_checked_at: Mapped[date] = mapped_column(Date, nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )


class MpDrugFeedback(TimestampMixin, Base):
    """药品留言板（认证医师留言板，演示期开放）。"""

    __tablename__ = "mp_drugs_feedback"
    __table_args__ = (Index("ix_mp_drugs_feedback_drug_id", "drug_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    drug_id: Mapped[int] = mapped_column(ForeignKey("mp_drugs.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(String(500), nullable=False)


class MpFavorite(TimestampMixin, Base):
    """药品收藏。"""

    __tablename__ = "mp_favorites"
    __table_args__ = (UniqueConstraint("user_id", "drug_id", name="uq_mp_favorites_user_drug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    drug_id: Mapped[int] = mapped_column(ForeignKey("mp_drugs.id", ondelete="CASCADE"), nullable=False)


class MpProfile(TimestampMixin, Base):
    """健康档案（五步闭环·第五步，一人一份）。"""

    __tablename__ = "mp_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)  # male | female
    birth: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)
    blood: Mapped[str | None] = mapped_column(String(10), nullable=True)
    chronic_tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    allergy: Mapped[str | None] = mapped_column(Text, nullable=True)
    surgery_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    smoke: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    drink: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sport_freq: Mapped[str | None] = mapped_column(String(20), nullable=True)


class MpProduct(TimestampMixin, Base):
    """健康商城商品（浏览骨架，status=coming）。"""

    __tablename__ = "mp_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # food|fsmp|package|device
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    desc: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)  # 单位：分
    status: Mapped[str] = mapped_column(String(20), default="coming", nullable=False)


# ───────────────────────── 并发防重（SQLite/测试路径） ─────────────────────────
# PostgreSQL 由迁移 a1c4f7e2b9d8 的部分唯一索引保证；SQLite 不支持
# postgresql_where，create_all 建表路径用 BEFORE 触发器实现同一不变量。

_APPT_SLOT_GUARD_SQL = """
CREATE TRIGGER IF NOT EXISTS mp_appointments_active_slot_guard
BEFORE INSERT ON mp_appointments
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'duplicate active appointment slot')
    WHERE NEW.status IN ('pending','confirmed')
      AND EXISTS (
        SELECT 1 FROM mp_appointments
        WHERE user_id = NEW.user_id
          AND appt_date = NEW.appt_date
          AND period = NEW.period
          AND status IN ('pending','confirmed')
          AND id != NEW.id
      );
END;
"""

_APPT_SLOT_GUARD_UPDATE_SQL = """
CREATE TRIGGER IF NOT EXISTS mp_appointments_active_slot_guard_update
BEFORE UPDATE OF user_id, appt_date, period, status ON mp_appointments
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'duplicate active appointment slot')
    WHERE NEW.status IN ('pending','confirmed')
      AND EXISTS (
        SELECT 1 FROM mp_appointments
        WHERE user_id = NEW.user_id
          AND appt_date = NEW.appt_date
          AND period = NEW.period
          AND status IN ('pending','confirmed')
          AND id != NEW.id
      );
END;
"""


@event.listens_for(Base.metadata, "after_create")
def _create_appointment_slot_guard(target, connection, **kw):  # pragma: no cover - 建表钩子
    """SQLite 建表后挂并发防重触发器；PostgreSQL/MySQL 走 Alembic 迁移。

    仅响应 Metadata 级事件（create_all 完成时 kw 不含 table）；
    mp_appointments 表缺失时静默跳过（部分建表场景）。
    """
    if kw.get("table") is not None:
        return
    if connection.dialect.name != "sqlite":
        return
    from sqlalchemy import inspect as sa_inspect, text

    if not sa_inspect(connection).has_table("mp_appointments"):
        return
    connection.execute(text(_APPT_SLOT_GUARD_SQL))
    connection.execute(text(_APPT_SLOT_GUARD_UPDATE_SQL))
