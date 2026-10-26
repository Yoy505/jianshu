"""全域健康管家服务包交易域模型（工作一，2026-10-21）。

覆盖：服务包与版本、订单 / 支付 / 支付事件 / 退款、积分账本、采购单位与查看账号、
采购批次与名单行、权益、授权记录。表名前缀 mp_，契约见 docs/api.md 第四部分。

关键不变量由数据库约束保证（见各表 __table_args__），服务层只负责把约束冲突映射为可读错误：
- 订单幂等 UNIQUE(user_id, client_order_id)；每单至多一份权益 UNIQUE(order_id)；
- 每名单行至多一份权益 UNIQUE(batch_row_id)；同单重复扣分 / 返还 UNIQUE(biz_type, order_id)；
- 积分余额与流水 balance_after 不为负；版本售出后不可修改由服务层校验。
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    event,
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


class PackageStatus(str, enum.Enum):
    DRAFT = "draft"
    ON_SHELF = "on_shelf"
    OFF_SHELF = "off_shelf"


class OrderPayType(str, enum.Enum):
    WXPAY = "wxpay"
    POINTS = "points"


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    CLOSED = "closed"
    REFUND_PENDING = "refund_pending"
    REFUND_FAILED = "refund_failed"
    REFUNDED = "refunded"


class PaymentStatus(str, enum.Enum):
    CREATED = "created"
    SUCCESS = "success"
    FAILED = "failed"


class PaymentEventType(str, enum.Enum):
    NOTIFY_ACCEPTED = "notify_accepted"
    NOTIFY_REPLAY = "notify_replay"
    NOTIFY_REJECTED = "notify_rejected"
    QUERY_SYNC = "query_sync"


class RefundChannel(str, enum.Enum):
    WXPAY = "wxpay"
    MANUAL = "manual"


class RefundStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


class PointsBizType(str, enum.Enum):
    ADMIN_ADJUST = "admin_adjust"
    REDEEM = "redeem"
    REFUND_REVERT = "refund_revert"


class BatchStatus(str, enum.Enum):
    PREVIEW = "preview"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class BatchRowStatus(str, enum.Enum):
    VALID = "valid"
    DUPLICATE = "duplicate"
    FORMAT_ERROR = "format_error"
    OVER_LIMIT = "over_limit"


class EntitlementSource(str, enum.Enum):
    ORDER = "order"
    BATCH = "batch"


class EntitlementStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    VOID = "void"


CONSENT_PURPOSE_PACKAGE_PURCHASE = "package_purchase"
# 履约域授权目的（工作二，2026-10-22）：约束放宽见迁移 e5c9a3f7b1d8
CONSENT_PURPOSE_STEWARD_SERVICE = "steward_service"
CONSENT_PURPOSE_STEWARD_DEVICE_DATA = "steward_device_data"
CONSENT_PURPOSE_STEWARD_PHONE_OUTREACH = "steward_phone_outreach"
CONSENT_PURPOSE_STEWARD_CONTACT_OUTREACH = "steward_contact_outreach"
CONSENT_PURPOSE_STEWARD_CALL_RECORDING = "steward_call_recording"


class MpPackage(TimestampMixin, Base):
    """服务包商品主体；价格 / 权益 / 有效期 / 条款都在版本表。"""

    __tablename__ = "mp_packages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'on_shelf', 'off_shelf')", name="ck_mp_packages_status"
        ),
        Index("ix_mp_packages_status_sort", "status", "sort", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=PackageStatus.DRAFT.value, server_default="draft", nullable=False
    )
    # 上架时指向的版本；与版本表互相引用，迁移中以 ALTER 追加外键
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "mp_package_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_mp_packages_current_version",
        ),
        nullable=True,
    )


class MpPackageVersion(TimestampMixin, Base):
    """服务包版本：一经创建不可修改，调价 / 改权益即建新版本；订单与权益绑定版本。"""

    __tablename__ = "mp_package_versions"
    __table_args__ = (
        UniqueConstraint("package_id", "version_no", name="uq_mp_package_versions_no"),
        CheckConstraint("price_cents > 0", name="ck_mp_package_versions_price"),
        CheckConstraint(
            "points_cost IS NULL OR points_cost > 0", name="ck_mp_package_versions_points"
        ),
        CheckConstraint(
            "validity_days >= 1 AND validity_days <= 3650", name="ck_mp_package_versions_validity"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("mp_packages.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    points_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validity_days: Mapped[int] = mapped_column(Integer, nullable=False)
    benefits: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    service_desc: Mapped[str] = mapped_column(Text, nullable=False)
    terms: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class MpOrder(TimestampMixin, Base):
    """订单是交易事实；支付成功只以服务端回调验签或主动查单为准。"""

    __tablename__ = "mp_orders"
    __table_args__ = (
        UniqueConstraint("user_id", "client_order_id", name="uq_mp_orders_client_key"),
        Index("ix_mp_orders_user_created", "user_id", "created_at"),
        Index("ix_mp_orders_status_created", "status", "created_at"),
        CheckConstraint("total_cents > 0", name="ck_mp_orders_total"),
        CheckConstraint("pay_type IN ('wxpay', 'points')", name="ck_mp_orders_pay_type"),
        CheckConstraint(
            "status IN ('pending_payment', 'paid', 'closed', 'refund_pending', "
            "'refund_failed', 'refunded')",
            name="ck_mp_orders_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("mp_packages.id", ondelete="RESTRICT"), nullable=False
    )
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("mp_package_versions.id", ondelete="RESTRICT"), nullable=False
    )
    package_name: Mapped[str] = mapped_column(String(100), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    pay_type: Mapped[str] = mapped_column(String(10), nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    points_cost: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=OrderStatus.PENDING_PAYMENT.value,
        server_default="pending_payment",
        nullable=False,
    )
    terms_version: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MpPayment(TimestampMixin, Base):
    """一次微信预下单尝试；transaction_id 唯一保证同一微信单号不重复记账。"""

    __tablename__ = "mp_payments"
    __table_args__ = (
        Index("ix_mp_payments_order", "order_id", "id"),
        UniqueConstraint("transaction_id", name="uq_mp_payments_transaction"),
        CheckConstraint("status IN ('created', 'success', 'failed')", name="ck_mp_payments_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("mp_orders.id", ondelete="CASCADE"), nullable=False)
    prepay_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), default=PaymentStatus.CREATED.value, server_default="created", nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    fail_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class MpPaymentEvent(TimestampMixin, Base):
    """回调 / 对账留痕：未知订单也记录（order_id 为空），detail 不含密钥或明文请求体。"""

    __tablename__ = "mp_payment_events"
    __table_args__ = (
        Index("ix_mp_payment_events_order", "order_id", "id"),
        Index("ix_mp_payment_events_out_trade_no", "out_trade_no"),
        CheckConstraint(
            "event_type IN ('notify_accepted', 'notify_replay', 'notify_rejected', 'query_sync')",
            name="ck_mp_payment_events_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("mp_orders.id", ondelete="SET NULL"), nullable=True
    )
    out_trade_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MpRefund(TimestampMixin, Base):
    """退款记录即人工处理队列；不直接改订单金额，结论登记后联动订单 / 权益 / 积分。"""

    __tablename__ = "mp_refunds"
    __table_args__ = (
        Index("ix_mp_refunds_order", "order_id", "id"),
        Index("ix_mp_refunds_status_created", "status", "created_at"),
        CheckConstraint("amount_cents > 0", name="ck_mp_refunds_amount"),
        CheckConstraint("channel IN ('wxpay', 'manual')", name="ck_mp_refunds_channel"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'success', 'failed')", name="ck_mp_refunds_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("mp_orders.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), default=RefundStatus.PENDING.value, server_default="pending", nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    wx_refund_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MpPointsAccount(Base):
    """积分余额行（一人一行）；余额变动必须伴随一条流水，并在同一事务内加行锁。"""

    __tablename__ = "mp_points_accounts"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_mp_points_accounts_balance"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MpPointsEntry(TimestampMixin, Base):
    """积分流水，只追加。UNIQUE(biz_type, order_id) 防止同一订单重复扣减或重复返还。"""

    __tablename__ = "mp_points_entries"
    __table_args__ = (
        Index("ix_mp_points_entries_user", "user_id", "id"),
        UniqueConstraint("biz_type", "order_id", name="uq_mp_points_entries_order_biz"),
        CheckConstraint("delta <> 0", name="ck_mp_points_entries_delta"),
        CheckConstraint("balance_after >= 0", name="ck_mp_points_entries_balance_after"),
        CheckConstraint(
            "biz_type IN ('admin_adjust', 'redeem', 'refund_revert')",
            name="ck_mp_points_entries_biz",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    biz_type: Mapped[str] = mapped_column(String(20), nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("mp_orders.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    operated_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )


class MpOrg(TimestampMixin, Base):
    """采购单位（企业 / 机构）。"""

    __tablename__ = "mp_orgs"
    __table_args__ = (CheckConstraint("status IN ('active', 'disabled')", name="ck_mp_orgs_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), default="active", server_default="active", nullable=False
    )


class MpOrgAccount(TimestampMixin, Base):
    """采购单位查看账号（scope=org）：只能读本单位批次汇总，看不到个人信息。"""

    __tablename__ = "mp_org_accounts"
    __table_args__ = (
        Index("ix_mp_org_accounts_org", "org_id"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_mp_org_accounts_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("mp_orgs.id", ondelete="CASCADE"), nullable=False)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), default="active", server_default="active", nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MpPurchaseBatch(TimestampMixin, Base):
    """采购批次：先预览后确认；确认前不产生权益。"""

    __tablename__ = "mp_purchase_batches"
    __table_args__ = (
        Index("ix_mp_purchase_batches_org", "org_id", "id"),
        Index("ix_mp_purchase_batches_status", "status", "created_at"),
        CheckConstraint("quantity >= 1 AND quantity <= 10000", name="ck_mp_purchase_batches_qty"),
        CheckConstraint(
            "status IN ('preview', 'confirmed', 'cancelled')", name="ck_mp_purchase_batches_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    org_id: Mapped[int] = mapped_column(ForeignKey("mp_orgs.id", ondelete="RESTRICT"), nullable=False)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("mp_packages.id", ondelete="RESTRICT"), nullable=False
    )
    package_version_id: Mapped[int] = mapped_column(
        ForeignKey("mp_package_versions.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # I2（2026-10-22）：file_sha256=原文件字节摘要（有原件时）；normalized_sha256=规范化号码数据摘要
    normalized_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stored_filename: Mapped[str | None] = mapped_column(String(68), nullable=True)
    status: Mapped[str] = mapped_column(
        String(12), default=BatchStatus.PREVIEW.value, server_default="preview", nullable=False
    )
    uploaded_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MpBatchRow(TimestampMixin, Base):
    """名单行：保存预览判定结果；只有 valid 行在确认时发权益。"""

    __tablename__ = "mp_batch_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_no", name="uq_mp_batch_rows_no"),
        Index("ix_mp_batch_rows_batch_status", "batch_id", "status"),
        CheckConstraint(
            "status IN ('valid', 'duplicate', 'format_error', 'over_limit')",
            name="ck_mp_batch_rows_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("mp_purchase_batches.id", ondelete="CASCADE"), nullable=False
    )
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_detail: Mapped[str | None] = mapped_column(String(200), nullable=True)


class MpEntitlement(TimestampMixin, Base):
    """权益：与订单或名单行一一对应；认领以手机号匹配本人微信核验号码。

    跨批次防重（R6，2026-10-22）：同单位同版本同手机号至多一份非 void 权益。
    - PostgreSQL：迁移 e5b8c2d4f6a1 部分唯一索引 uq_mp_entitlements_batch_phone；
    - SQLite（测试 create_all 路径）：文件末尾 DDL 事件建等价 BEFORE 触发器。
    """

    __tablename__ = "mp_entitlements"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_mp_entitlements_order"),
        UniqueConstraint("batch_row_id", name="uq_mp_entitlements_batch_row"),
        Index("ix_mp_entitlements_user", "user_id", "id"),
        Index("ix_mp_entitlements_phone_status", "phone", "status"),
        Index("ix_mp_entitlements_batch", "batch_id"),
        Index("ix_mp_entitlements_batch_phone", "org_id", "package_version_id", "phone"),
        CheckConstraint("source_type IN ('order', 'batch')", name="ck_mp_entitlements_source"),
        CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'void')", name="ck_mp_entitlements_status"
        ),
        CheckConstraint(
            "(source_type = 'order' AND order_id IS NOT NULL AND batch_row_id IS NULL) OR "
            "(source_type = 'batch' AND batch_row_id IS NOT NULL AND order_id IS NULL)",
            name="ck_mp_entitlements_source_link",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(10), nullable=False)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("mp_orders.id", ondelete="RESTRICT"), nullable=True
    )
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("mp_purchase_batches.id", ondelete="RESTRICT"), nullable=True
    )
    batch_row_id: Mapped[int | None] = mapped_column(
        ForeignKey("mp_batch_rows.id", ondelete="RESTRICT"), nullable=True
    )
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("mp_orgs.id", ondelete="RESTRICT"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MpSaleRelease(TimestampMixin, Base):
    """开售放行记录（R8）：商品/版本/环境 + 负责人 + 依据 + 撤销留痕。

    默认不放行：无有效（未撤销且环境匹配）放行记录的服务包在 C 端不可购买，
    微信与积分下单在服务端同一判定，旧页面或直接 API 提交不能绕过。
    """

    __tablename__ = "mp_sale_releases"
    __table_args__ = (
        Index("ix_mp_sale_releases_pkg", "package_id", "version_id"),
        CheckConstraint(
            "env IN ('dev', 'test', 'staging', 'prod', 'all')", name="ck_mp_sale_releases_env"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    package_id: Mapped[int] = mapped_column(
        ForeignKey("mp_packages.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("mp_package_versions.id", ondelete="CASCADE"), nullable=False
    )
    env: Mapped[str] = mapped_column(String(10), nullable=False)
    basis: Mapped[str] = mapped_column(String(500), nullable=False)
    released_by_admin_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    released_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class MpUserConsent(TimestampMixin, Base):
    """购买授权记录：目的 / 版本 / 文本快照 / 授权与撤回时间；撤回不删除记录。"""

    __tablename__ = "mp_user_consents"
    __table_args__ = (
        Index("ix_mp_user_consents_user_purpose", "user_id", "purpose", "id"),
        CheckConstraint(
            "purpose IN ('package_purchase', 'steward_service', 'steward_device_data', "
            "'steward_phone_outreach', 'steward_contact_outreach', 'steward_call_recording')",
            name="ck_mp_user_consents_purpose",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), default="order_confirm", server_default="order_confirm", nullable=False
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("mp_orders.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ───────────────────────── 权益变更事件（工作二消费接口） ─────────────────────────

ENTITLEMENT_EVENT_TYPES = (
    "issued",      # 发放（订单支付 / 兑换 / 批次确认）
    "claimed",     # 本人认领
    "rejected",    # 本人拒绝
    "reclaimed",   # 拒绝后再次认领
    "reassigned",  # 后台改号
    "voided",      # 作废（人工 / 退款联动）
)


class MpEntitlementEvent(TimestampMixin, Base):
    """权益变更事件，只追加：工作二据此终止/调整服务实例；detail 不含手机号全号。"""

    __tablename__ = "mp_entitlement_events"
    __table_args__ = (
        Index("ix_mp_entitlement_events_ent", "entitlement_id", "id"),
        CheckConstraint(
            "event_type IN ('issued', 'claimed', 'rejected', 'reclaimed', 'reassigned', 'voided')",
            name="ck_mp_entitlement_events_type",
        ),
        CheckConstraint(
            "actor_type IN ('user', 'admin', 'system')",
            name="ck_mp_entitlement_events_actor",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entitlement_id: Mapped[int] = mapped_column(
        ForeignKey("mp_entitlements.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(10), default="system", nullable=False)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ───────────────────────── 跨批次防重（SQLite/测试路径） ─────────────────────────
# PostgreSQL 由迁移 e5b8c2d4f6a1 的部分唯一索引保证；SQLite 不支持
# postgresql_where，create_all 建表路径用 BEFORE 触发器实现同一不变量。

_ENT_BATCH_PHONE_GUARD_INSERT_SQL = """
CREATE TRIGGER IF NOT EXISTS mp_entitlements_batch_phone_guard
BEFORE INSERT ON mp_entitlements
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'duplicate batch entitlement phone')
    WHERE NEW.source_type = 'batch'
      AND NEW.status <> 'void'
      AND NEW.phone IS NOT NULL
      AND NEW.org_id IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM mp_entitlements
        WHERE source_type = 'batch'
          AND status <> 'void'
          AND phone = NEW.phone
          AND org_id = NEW.org_id
          AND package_version_id = NEW.package_version_id
          AND id != NEW.id
      );
END;
"""

_ENT_BATCH_PHONE_GUARD_UPDATE_SQL = """
CREATE TRIGGER IF NOT EXISTS mp_entitlements_batch_phone_guard_update
BEFORE UPDATE OF org_id, package_version_id, phone, status ON mp_entitlements
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'duplicate batch entitlement phone')
    WHERE NEW.source_type = 'batch'
      AND NEW.status <> 'void'
      AND NEW.phone IS NOT NULL
      AND NEW.org_id IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM mp_entitlements
        WHERE source_type = 'batch'
          AND status <> 'void'
          AND phone = NEW.phone
          AND org_id = NEW.org_id
          AND package_version_id = NEW.package_version_id
          AND id != NEW.id
      );
END;
"""


@event.listens_for(Base.metadata, "after_create")
def _create_entitlement_batch_phone_guard(target, connection, **kw):  # pragma: no cover - 建表钩子
    """SQLite 建表后挂跨批次防重触发器；PostgreSQL 走 Alembic 部分唯一索引。"""
    if kw.get("table") is not None:
        return
    if connection.dialect.name != "sqlite":
        return
    from sqlalchemy import inspect as sa_inspect, text

    if not sa_inspect(connection).has_table("mp_entitlements"):
        return
    connection.execute(text(_ENT_BATCH_PHONE_GUARD_INSERT_SQL))
    connection.execute(text(_ENT_BATCH_PHONE_GUARD_UPDATE_SQL))
