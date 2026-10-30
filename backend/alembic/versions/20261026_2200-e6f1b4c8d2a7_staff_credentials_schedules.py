"""staff credentials & schedules（C2：资质核验与排班覆盖）

steward_staff 新增资质核验字段：审核材料私有引用 credential_ref、来源 credential_source、
核验人 / 时间 credential_verified_by_admin_id / credential_verified_at、有效期 credential_valid_until、
撤销 credential_revoked_at / credential_revoke_reason，以及服务范围 service_scope。
新表 steward_staff_schedules（人员排班区间，取消留痕）。

激活门禁 staffing_ready 自此要求：三岗在职且资质有效（已核验、未撤销、未到期），负责管家与
替补在激活当日各有有效排班。存量人员无核验记录 → 尚未激活的实例门禁按事实显示缺项，
已激活实例不受影响（不以"已指派三人"代替资质与排班）。

Revision ID: e6f1b4c8d2a7
Revises: d5e9a3b7c1f4
Create Date: 2026-10-26 22:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "e6f1b4c8d2a7"
down_revision = "d5e9a3b7c1f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("steward_staff") as batch_op:
        batch_op.add_column(sa.Column("service_scope", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("credential_ref", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("credential_source", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("credential_verified_by_admin_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("credential_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("credential_valid_until", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("credential_revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("credential_revoke_reason", sa.String(length=200), nullable=True))
        batch_op.create_foreign_key(
            "fk_steward_staff_credential_verified_by", "admin_users",
            ["credential_verified_by_admin_id"], ["id"], ondelete="SET NULL",
        )
    op.create_table(
        "steward_staff_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("steward_staff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="active", nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=200), nullable=True),
        sa.Column(
            "created_by_admin_id", sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL", name="fk_steward_staff_schedules_created_by"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("status IN ('active', 'cancelled')", name="ck_steward_staff_schedules_status"),
        sa.CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="ck_steward_staff_schedules_range"),
    )
    op.create_index("ix_steward_staff_schedules_staff", "steward_staff_schedules", ["staff_id", "starts_on"])


def downgrade() -> None:
    op.drop_index("ix_steward_staff_schedules_staff", table_name="steward_staff_schedules")
    op.drop_table("steward_staff_schedules")
    with op.batch_alter_table("steward_staff") as batch_op:
        batch_op.drop_constraint("fk_steward_staff_credential_verified_by", type_="foreignkey")
        for column in (
            "credential_revoke_reason", "credential_revoked_at", "credential_valid_until",
            "credential_verified_at", "credential_verified_by_admin_id", "credential_source",
            "credential_ref", "service_scope",
        ):
            batch_op.drop_column(column)
