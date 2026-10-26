"""服务包交易域 DTO（契约 docs/api.md 第四部分）。响应不直接暴露 ORM，敏感字段掩码。"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Page, UTCDateTime

PayType = Literal["wxpay", "points"]
RefundChannelType = Literal["wxpay", "manual"]


# ───────────────────────── 公共片段 ─────────────────────────


class BenefitItem(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    desc: str | None = Field(default=None, max_length=200)


class Purchasable(BaseModel):
    wxpay: bool
    points: bool
    reasons: list[str]


class PackageRef(BaseModel):
    id: int
    name: str
    version_id: int
    version_no: int


# ───────────────────────── C 端 · 服务包 ─────────────────────────


class CapabilitiesOut(BaseModel):
    wxpay_enabled: bool
    points_enabled: bool = True
    wxpay_reason: str | None = None


class PackageSummaryOut(BaseModel):
    id: int
    name: str
    subtitle: str | None
    cover_url: str | None
    price_cents: int
    points_cost: int | None
    validity_days: int
    benefit_count: int
    purchasable: Purchasable


class PackageDetailOut(PackageSummaryOut):
    version_id: int
    version_no: int
    benefits: list[BenefitItem]
    service_desc: str
    terms: str
    terms_version: str


# ───────────────────────── C 端 · 订单 ─────────────────────────


class OrderCreateIn(BaseModel):
    client_order_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9\-_]+$")
    package_id: int
    version_id: int  # 用户实际确认的版本；与当前上架版本不一致返回 409 要求重新确认
    pay_type: PayType
    agree_terms: bool


class RefundOut(BaseModel):
    id: int
    refund_no: str
    channel: str
    status: str
    amount_cents: int
    reason: str
    note: str | None
    created_at: UTCDateTime
    finished_at: UTCDateTime | None


class OrderOut(BaseModel):
    id: int
    order_no: str
    status: str
    pay_type: str
    package: PackageRef
    total_cents: int
    points_cost: int | None
    expires_at: UTCDateTime | None
    paid_at: UTCDateTime | None
    closed_at: UTCDateTime | None
    entitlement_id: int | None
    created_at: UTCDateTime


class OrderDetailOut(OrderOut):
    refunds: list[RefundOut]


class PrepayOut(BaseModel):
    timeStamp: str
    nonceStr: str
    package: str
    signType: str
    paySign: str


# ───────────────────────── C 端 · 积分 / 权益 / 授权 ─────────────────────────


class PointsAccountOut(BaseModel):
    balance: int
    updated_at: UTCDateTime | None


class PointsEntryOut(BaseModel):
    id: int
    delta: int
    balance_after: int
    biz_type: str
    order_id: int | None
    note: str | None
    created_at: UTCDateTime


class EntitlementPackageRef(BaseModel):
    id: int
    name: str
    version_no: int


class EntitlementOut(BaseModel):
    id: int
    source_type: str
    status: str
    package: EntitlementPackageRef
    benefits: list[BenefitItem]
    validity_days: int
    activated_at: UTCDateTime | None
    valid_until: UTCDateTime | None
    org_name: str | None
    order_id: int | None
    created_at: UTCDateTime


class ConsentOut(BaseModel):
    id: int
    purpose: str
    version: str
    text: str
    source: str = "order_confirm"
    granted_at: UTCDateTime
    revoked_at: UTCDateTime | None


# ───────────────────────── 采购单位查看端 ─────────────────────────


class OrgLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class OrgBrief(BaseModel):
    id: int
    name: str


class OrgAccountBrief(BaseModel):
    id: int
    username: str


class OrgLoginOut(BaseModel):
    access_token: str
    refresh_token: str
    org: OrgBrief
    account: OrgAccountBrief


class OrgMeOut(BaseModel):
    org: OrgBrief
    account: OrgAccountBrief


class BatchPackageRef(BaseModel):
    id: int
    name: str
    version_no: int


class BatchSummary(BaseModel):
    total: int
    valid: int
    duplicate: int
    format_error: int
    over_limit: int


class OrgBatchOut(BaseModel):
    """单位视图：只有计数，无任何个人信息。"""

    id: int
    batch_no: str
    package: BatchPackageRef
    quantity: int
    valid_count: int
    issued_count: int
    claimed_count: int
    rejected_count: int
    pending_count: int
    status: str
    confirmed_at: UTCDateTime | None
    created_at: UTCDateTime


# ───────────────────────── 后台 · 服务包 ─────────────────────────


class PackageCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    subtitle: str | None = Field(default=None, max_length=200)
    cover_url: str | None = Field(default=None, max_length=500)
    sort: int = Field(default=0, ge=0, le=9999)


class PackagePatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    subtitle: str | None = Field(default=None, max_length=200)
    cover_url: str | None = Field(default=None, max_length=500)
    sort: int | None = Field(default=None, ge=0, le=9999)


class VersionCreateIn(BaseModel):
    price_cents: int = Field(gt=0, le=100_000_000)
    points_cost: int | None = Field(default=None, gt=0, le=100_000_000)
    validity_days: int = Field(ge=1, le=3650)
    benefits: list[BenefitItem] = Field(min_length=1, max_length=20)
    service_desc: str = Field(min_length=1, max_length=5000)
    terms: str = Field(min_length=1, max_length=5000)

    @field_validator("service_desc", "terms")
    @classmethod
    def _strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("内容不能为空白")
        return stripped


class ShelfIn(BaseModel):
    on_shelf: bool
    version_id: int | None = None


class VersionOut(BaseModel):
    id: int
    version_no: int
    price_cents: int
    points_cost: int | None
    validity_days: int
    benefits: list[BenefitItem]
    service_desc: str
    terms: str
    created_by: str | None
    created_at: UTCDateTime


class AdminPackageOut(BaseModel):
    id: int
    name: str
    subtitle: str | None
    cover_url: str | None
    sort: int
    status: str
    current_version: VersionOut | None
    blockers: list[str]
    created_at: UTCDateTime
    updated_at: UTCDateTime


class AdminPackageDetailOut(AdminPackageOut):
    versions: list[VersionOut]
    releases: list["ReleaseOut"]


# ───────────────────────── 后台 · 开售放行（R8） ─────────────────────────


ReleaseEnv = Literal["dev", "test", "staging", "prod", "all"]


class ReleaseCreateIn(BaseModel):
    version_id: int
    env: ReleaseEnv
    basis: str = Field(min_length=1, max_length=500)


class ReleaseRevokeIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ReleaseOut(BaseModel):
    id: int
    package_id: int
    version_id: int
    version_no: int
    env: str
    basis: str
    released_by: str | None
    released_at: UTCDateTime
    revoked_at: UTCDateTime | None
    revoked_by: str | None
    revoke_reason: str | None


# ───────────────────────── 后台 · 订单 / 退款 / 积分 ─────────────────────────


class UserBrief(BaseModel):
    id: int
    nickname: str
    phone: str | None  # 掩码


class PaymentOut(BaseModel):
    id: int
    prepay_id: str | None
    transaction_id: str | None
    status: str
    amount_cents: int
    fail_reason: str | None
    created_at: UTCDateTime


class PaymentEventOut(BaseModel):
    id: int
    event_type: str
    result: str
    detail: dict | None
    created_at: UTCDateTime


class AdminOrderOut(OrderOut):
    user: UserBrief


class AdminOrderDetailOut(AdminOrderOut):
    payments: list[PaymentOut]
    events: list[PaymentEventOut]
    refunds: list[RefundOut]
    entitlement: EntitlementOut | None


class RefundCreateIn(BaseModel):
    channel: RefundChannelType
    amount_cents: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=200)


class RefundResolveIn(BaseModel):
    status: Literal["success", "failed"]
    note: str | None = Field(default=None, max_length=200)


class AdminRefundOut(RefundOut):
    order_id: int
    order_no: str
    pay_type: str
    user: UserBrief
    requested_by: str | None
    resolved_by: str | None


class PointsAdjustIn(BaseModel):
    user_id: int
    delta: int = Field(ge=-1_000_000, le=1_000_000)
    note: str = Field(min_length=1, max_length=200)

    @field_validator("delta")
    @classmethod
    def _nonzero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("调整额不能为 0")
        return value


class AdminPointsOut(BaseModel):
    user: UserBrief
    balance: int
    entries: Page[PointsEntryOut]


# ───────────────────────── 后台 · 单位 / 批次 / 权益 ─────────────────────────


class OrgCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    contact_name: str | None = Field(default=None, max_length=50)
    contact_phone: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=500)


class OrgOut(BaseModel):
    id: int
    name: str
    contact_name: str | None
    contact_phone: str | None  # 掩码
    note: str | None
    status: str
    account_count: int
    created_at: UTCDateTime


class OrgAccountCreateIn(BaseModel):
    username: str = Field(min_length=4, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    password: str = Field(min_length=10, max_length=128)


class OrgAccountOut(BaseModel):
    id: int
    org_id: int
    username: str
    status: str
    created_at: UTCDateTime


class BatchPreviewIn(BaseModel):
    org_id: int
    package_id: int
    version_id: int
    quantity: int = Field(ge=1, le=10000)
    file_name: str = Field(min_length=1, max_length=255)
    rows: list[str] = Field(min_length=1, max_length=10000)


class BatchRowOut(BaseModel):
    row_no: int
    phone: str  # 掩码
    status: str
    error_detail: str | None
    entitlement_status: str | None = None


class BatchOut(BaseModel):
    id: int
    batch_no: str
    org: OrgBrief
    package: BatchPackageRef
    quantity: int
    file_name: str
    file_sha256: str
    status: str
    summary: BatchSummary
    issued_count: int
    claimed_count: int
    uploaded_by: str | None
    confirmed_by: str | None
    confirmed_at: UTCDateTime | None
    created_at: UTCDateTime


class BatchDetailOut(BatchOut):
    rows: list[BatchRowOut]


class BatchPreviewOut(BaseModel):
    batch: BatchOut
    rows: list[BatchRowOut]
    summary: BatchSummary


class AdminEntitlementOut(EntitlementOut):
    phone: str | None  # 掩码
    batch_id: int | None
    user: UserBrief | None
    void_reason: str | None


class EntitlementVoidIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class EntitlementReassignIn(BaseModel):
    phone: str = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=200)
