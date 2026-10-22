"""commerce service packages: packages, orders, payments, points, batches, entitlements

工作一（全域健康管家服务包）交易域 12 张表。

升级：全新增表，无存量数据改写；回滚：按依赖逆序全部删除，不影响既有域。
PostgreSQL 与 SQLite 均可执行（循环外键用 batch_alter_table 延迟创建）。

Revision ID: d4a7b1c2e3f5
Revises: c3f8d2e5a928
Create Date: 2026-10-21 10:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a7b1c2e3f5"
down_revision = "c3f8d2e5a928"
branch_labels = None
depends_on = None


def _ts_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "mp_packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("subtitle", sa.String(200), nullable=True),
        sa.Column("cover_url", sa.String(500), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.CheckConstraint(
            "status IN ('draft', 'on_shelf', 'off_shelf')", name="ck_mp_packages_status"
        ),
    )
    op.create_index(
        "ix_mp_packages_status_sort", "mp_packages", ["status", "sort", "id"]
    )

    op.create_table(
        "mp_package_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "package_id",
            sa.Integer(),
            sa.ForeignKey("mp_packages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("points_cost", sa.Integer(), nullable=True),
        sa.Column("validity_days", sa.Integer(), nullable=False),
        sa.Column("benefits", sa.JSON(), nullable=False),
        sa.Column("service_desc", sa.Text(), nullable=False),
        sa.Column("terms", sa.Text(), nullable=False),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_ts_columns(),
        sa.UniqueConstraint("package_id", "version_no", name="uq_mp_package_versions_no"),
        sa.CheckConstraint("price_cents > 0", name="ck_mp_package_versions_price"),
        sa.CheckConstraint(
            "points_cost IS NULL OR points_cost > 0", name="ck_mp_package_versions_points"
        ),
        sa.CheckConstraint(
            "validity_days >= 1 AND validity_days <= 3650", name="ck_mp_package_versions_validity"
        ),
    )

    # packages.current_version_id → versions（互相引用，延迟建外键；batch 兼容 SQLite）
    with op.batch_alter_table("mp_packages") as batch_op:
        batch_op.create_foreign_key(
            "fk_mp_packages_current_version",
            "mp_package_versions",
            ["current_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "mp_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_no", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("client_order_id", sa.String(64), nullable=False),
        sa.Column(
            "package_id",
            sa.Integer(),
            sa.ForeignKey("mp_packages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "package_version_id",
            sa.Integer(),
            sa.ForeignKey("mp_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("package_name", sa.String(100), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("pay_type", sa.String(10), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("points_cost", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_payment"),
        sa.Column("terms_version", sa.String(20), nullable=False),
        sa.Column("transaction_id", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.UniqueConstraint("user_id", "client_order_id", name="uq_mp_orders_client_key"),
        sa.CheckConstraint("total_cents > 0", name="ck_mp_orders_total"),
        sa.CheckConstraint("pay_type IN ('wxpay', 'points')", name="ck_mp_orders_pay_type"),
        sa.CheckConstraint(
            "status IN ('pending_payment', 'paid', 'closed', 'refund_pending', "
            "'refund_failed', 'refunded')",
            name="ck_mp_orders_status",
        ),
    )
    op.create_index("ix_mp_orders_user_created", "mp_orders", ["user_id", "created_at"])
    op.create_index("ix_mp_orders_status_created", "mp_orders", ["status", "created_at"])

    op.create_table(
        "mp_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("mp_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prepay_id", sa.String(128), nullable=True),
        sa.Column("transaction_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="created"),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("fail_reason", sa.String(200), nullable=True),
        *_ts_columns(),
        sa.UniqueConstraint("transaction_id", name="uq_mp_payments_transaction"),
        sa.CheckConstraint("status IN ('created', 'success', 'failed')", name="ck_mp_payments_status"),
    )
    op.create_index("ix_mp_payments_order", "mp_payments", ["order_id", "id"])

    op.create_table(
        "mp_payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("mp_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("out_trade_no", sa.String(32), nullable=True),
        sa.Column("transaction_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("result", sa.String(40), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        *_ts_columns(),
        sa.CheckConstraint(
            "event_type IN ('notify_accepted', 'notify_replay', 'notify_rejected', 'query_sync')",
            name="ck_mp_payment_events_type",
        ),
    )
    op.create_index("ix_mp_payment_events_order", "mp_payment_events", ["order_id", "id"])
    op.create_index(
        "ix_mp_payment_events_out_trade_no", "mp_payment_events", ["out_trade_no"]
    )

    op.create_table(
        "mp_refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("refund_no", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("mp_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("wx_refund_id", sa.String(64), nullable=True),
        sa.Column(
            "requested_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "resolved_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.CheckConstraint("amount_cents > 0", name="ck_mp_refunds_amount"),
        sa.CheckConstraint("channel IN ('wxpay', 'manual')", name="ck_mp_refunds_channel"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'success', 'failed')", name="ck_mp_refunds_status"
        ),
    )
    op.create_index("ix_mp_refunds_order", "mp_refunds", ["order_id", "id"])
    op.create_index("ix_mp_refunds_status_created", "mp_refunds", ["status", "created_at"])

    op.create_table(
        "mp_points_accounts",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("balance >= 0", name="ck_mp_points_accounts_balance"),
    )

    op.create_table(
        "mp_points_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("biz_type", sa.String(20), nullable=False),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("mp_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column(
            "operated_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_ts_columns(),
        sa.UniqueConstraint("biz_type", "order_id", name="uq_mp_points_entries_order_biz"),
        sa.CheckConstraint("delta <> 0", name="ck_mp_points_entries_delta"),
        sa.CheckConstraint("balance_after >= 0", name="ck_mp_points_entries_balance_after"),
        sa.CheckConstraint(
            "biz_type IN ('admin_adjust', 'redeem', 'refund_revert')",
            name="ck_mp_points_entries_biz",
        ),
    )
    op.create_index("ix_mp_points_entries_user", "mp_points_entries", ["user_id", "id"])

    op.create_table(
        "mp_orgs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("contact_name", sa.String(50), nullable=True),
        sa.Column("contact_phone", sa.String(20), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        *_ts_columns(),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_mp_orgs_status"),
    )

    op.create_table(
        "mp_org_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "org_id", sa.Integer(), sa.ForeignKey("mp_orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_mp_org_accounts_status"),
    )
    op.create_index("ix_mp_org_accounts_org", "mp_org_accounts", ["org_id"])

    op.create_table(
        "mp_purchase_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_no", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "org_id", sa.Integer(), sa.ForeignKey("mp_orgs.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "package_id",
            sa.Integer(),
            sa.ForeignKey("mp_packages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "package_version_id",
            sa.Integer(),
            sa.ForeignKey("mp_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="preview"),
        sa.Column(
            "uploaded_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "confirmed_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.CheckConstraint("quantity >= 1 AND quantity <= 10000", name="ck_mp_purchase_batches_qty"),
        sa.CheckConstraint(
            "status IN ('preview', 'confirmed', 'cancelled')", name="ck_mp_purchase_batches_status"
        ),
    )
    op.create_index("ix_mp_purchase_batches_org", "mp_purchase_batches", ["org_id", "id"])
    op.create_index(
        "ix_mp_purchase_batches_status", "mp_purchase_batches", ["status", "created_at"]
    )

    op.create_table(
        "mp_batch_rows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("mp_purchase_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_no", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_detail", sa.String(200), nullable=True),
        *_ts_columns(),
        sa.UniqueConstraint("batch_id", "row_no", name="uq_mp_batch_rows_no"),
        sa.CheckConstraint(
            "status IN ('valid', 'duplicate', 'format_error', 'over_limit')",
            name="ck_mp_batch_rows_status",
        ),
    )
    op.create_index("ix_mp_batch_rows_batch_status", "mp_batch_rows", ["batch_id", "status"])

    op.create_table(
        "mp_entitlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(10), nullable=False),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("mp_orders.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("mp_purchase_batches.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "batch_row_id",
            sa.Integer(),
            sa.ForeignKey("mp_batch_rows.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column(
            "package_id",
            sa.Integer(),
            sa.ForeignKey("mp_packages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "package_version_id",
            sa.Integer(),
            sa.ForeignKey("mp_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("package_name", sa.String(100), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("benefits", sa.JSON(), nullable=False),
        sa.Column("validity_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.String(200), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.UniqueConstraint("order_id", name="uq_mp_entitlements_order"),
        sa.UniqueConstraint("batch_row_id", name="uq_mp_entitlements_batch_row"),
        sa.CheckConstraint("source_type IN ('order', 'batch')", name="ck_mp_entitlements_source"),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'void')", name="ck_mp_entitlements_status"
        ),
        sa.CheckConstraint(
            "(source_type = 'order' AND order_id IS NOT NULL AND batch_row_id IS NULL) OR "
            "(source_type = 'batch' AND batch_row_id IS NOT NULL AND order_id IS NULL)",
            name="ck_mp_entitlements_source_link",
        ),
    )
    op.create_index("ix_mp_entitlements_user", "mp_entitlements", ["user_id", "id"])
    op.create_index(
        "ix_mp_entitlements_phone_status", "mp_entitlements", ["phone", "status"]
    )
    op.create_index("ix_mp_entitlements_batch", "mp_entitlements", ["batch_id"])

    op.create_table(
        "mp_user_consents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("mp_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("purpose IN ('package_purchase')", name="ck_mp_user_consents_purpose"),
    )
    op.create_index(
        "ix_mp_user_consents_user_purpose", "mp_user_consents", ["user_id", "purpose", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_mp_user_consents_user_purpose", table_name="mp_user_consents")
    op.drop_table("mp_user_consents")
    op.drop_index("ix_mp_entitlements_batch", table_name="mp_entitlements")
    op.drop_index("ix_mp_entitlements_phone_status", table_name="mp_entitlements")
    op.drop_index("ix_mp_entitlements_user", table_name="mp_entitlements")
    op.drop_table("mp_entitlements")
    op.drop_index("ix_mp_batch_rows_batch_status", table_name="mp_batch_rows")
    op.drop_table("mp_batch_rows")
    op.drop_index("ix_mp_purchase_batches_status", table_name="mp_purchase_batches")
    op.drop_index("ix_mp_purchase_batches_org", table_name="mp_purchase_batches")
    op.drop_table("mp_purchase_batches")
    op.drop_index("ix_mp_org_accounts_org", table_name="mp_org_accounts")
    op.drop_table("mp_org_accounts")
    op.drop_table("mp_orgs")
    op.drop_index("ix_mp_points_entries_user", table_name="mp_points_entries")
    op.drop_table("mp_points_entries")
    op.drop_table("mp_points_accounts")
    op.drop_index("ix_mp_refunds_status_created", table_name="mp_refunds")
    op.drop_index("ix_mp_refunds_order", table_name="mp_refunds")
    op.drop_table("mp_refunds")
    op.drop_index("ix_mp_payment_events_out_trade_no", table_name="mp_payment_events")
    op.drop_index("ix_mp_payment_events_order", table_name="mp_payment_events")
    op.drop_table("mp_payment_events")
    op.drop_index("ix_mp_payments_order", table_name="mp_payments")
    op.drop_table("mp_payments")
    op.drop_index("ix_mp_orders_status_created", table_name="mp_orders")
    op.drop_index("ix_mp_orders_user_created", table_name="mp_orders")
    op.drop_table("mp_orders")
    with op.batch_alter_table("mp_packages") as batch_op:
        batch_op.drop_constraint("fk_mp_packages_current_version", type_="foreignkey")
    op.drop_table("mp_package_versions")
    op.drop_index("ix_mp_packages_status_sort", table_name="mp_packages")
    op.drop_table("mp_packages")
