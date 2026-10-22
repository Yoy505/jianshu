"""device domain: vendors, bindings, sync cursors, metrics, sync logs (slice C)

工作二切片 C（2026-10-22）：标准设备数据域 5 表 + 门禁数据修正——
device_paired / first_valid_data 两项 prep_check 由 manual 转 auto（切片 C 起按
绑定与有效数据事实判定）；既有 manual 行重置为 auto/pending（原人工凭据不自动
视作设备事实，如实重判）。

升级：全新增表 + 一处门禁行数据修正（source/status/evidence 清理）；
回滚：删 5 表；门禁行仅翻回 manual（evidence 已清理不可恢复，见 down 说明）。
PostgreSQL 与 SQLite 均可执行。

Revision ID: b6d3f8a2c9e4
Revises: a2c8e4f1b7d3
Create Date: 2026-10-22 19:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "b6d3f8a2c9e4"
down_revision = "a2c8e4f1b7d3"
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
        "device_vendors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("code", name="uq_device_vendors_code"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_device_vendors_status"),
    )

    op.create_table(
        "device_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("device_model", sa.String(50), nullable=False),
        sa.Column("device_sn", sa.String(64), nullable=False),
        sa.Column("active_flag", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column(
            "bound_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("unbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unbind_reason", sa.String(200), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["vendor_id"], ["device_vendors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("vendor_id", "device_sn", "active_flag", name="uq_device_bindings_active_device"),
        sa.CheckConstraint("status IN ('active', 'unbound')", name="ck_device_bindings_status"),
    )
    op.create_index("ix_device_bindings_user", "device_bindings", ["user_id", "status", "id"])

    op.create_table(
        "device_sync_cursors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False, server_default="idle"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["binding_id"], ["device_bindings.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("binding_id", name="uq_device_sync_cursors_binding"),
        sa.CheckConstraint("status IN ('idle', 'blocked')", name="ck_device_sync_cursors_status"),
        sa.CheckConstraint("failure_count >= 0", name="ck_device_sync_cursors_failures"),
    )

    op.create_table(
        "device_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("metric_type", sa.String(20), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit", sa.String(10), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("tz_offset_minutes", sa.Integer(), nullable=False, server_default="480"),
        sa.Column("dedup_key", sa.String(128), nullable=False),
        sa.Column("vendor_record_id", sa.String(64), nullable=False),
        sa.Column("vendor_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("quality", sa.String(16), nullable=False, server_default="valid"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("review_note", sa.String(200), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["binding_id"], ["device_bindings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("binding_id", "dedup_key", "revision", name="uq_device_metrics_rev"),
        sa.CheckConstraint(
            "quality IN ('valid', 'invalid_value', 'late', 'reviewed')",
            name="ck_device_metrics_quality",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_device_metrics_revision"),
    )
    op.create_index("ix_device_metrics_binding_date", "device_metrics", ["binding_id", "business_date"])
    op.create_index(
        "ix_device_metrics_user_metric", "device_metrics", ["user_id", "metric_type", "business_date"]
    )

    op.create_table(
        "device_sync_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("pulled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revisions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("late", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wrong_user", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["binding_id"], ["device_bindings.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('success', 'partial', 'failed', 'blocked')",
            name="ck_device_sync_logs_status",
        ),
    )
    op.create_index("ix_device_sync_logs_binding", "device_sync_logs", ["binding_id", "id"])
    op.create_index("ix_device_sync_logs_status", "device_sync_logs", ["status", "id"])

    # 门禁数据修正：device_paired / first_valid_data manual → auto/pending（如实重判；
    # 原人工凭据不自动视作设备事实）。down 无法恢复已清理的 evidence，见 downgrade 注释。
    op.execute(
        "UPDATE steward_prep_checks SET source = 'auto', status = 'pending', "
        "missing_reason = NULL, evidence = NULL, completed_at = NULL, completed_by_admin_id = NULL "
        "WHERE check_type IN ('device_paired', 'first_valid_data') AND source = 'manual'"
    )


def downgrade() -> None:
    # 注意：升级时清理的 manual 凭据不可恢复；降级仅翻回 manual/pending。
    op.execute(
        "UPDATE steward_prep_checks SET source = 'manual', status = 'pending', "
        "missing_reason = '降级回退：需人工重新录入凭据' "
        "WHERE check_type IN ('device_paired', 'first_valid_data') AND source = 'auto'"
    )
    op.drop_index("ix_device_sync_logs_status", table_name="device_sync_logs")
    op.drop_index("ix_device_sync_logs_binding", table_name="device_sync_logs")
    op.drop_table("device_sync_logs")
    op.drop_index("ix_device_metrics_user_metric", table_name="device_metrics")
    op.drop_index("ix_device_metrics_binding_date", table_name="device_metrics")
    op.drop_table("device_metrics")
    op.drop_table("device_sync_cursors")
    op.drop_index("ix_device_bindings_user", table_name="device_bindings")
    op.drop_table("device_bindings")
    op.drop_table("device_vendors")
