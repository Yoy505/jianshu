"""phone domain: scripts, contacts, call orders, attempts (slice E) + consent purpose widening

工作二切片 E（2026-10-22）：电话域 4 表；同时放宽 steward_consent_terms.purpose 与
mp_user_consents.purpose 约束（新增 steward_phone_outreach / steward_contact_outreach /
steward_call_recording 三类分立授权）。

升级：全新增表 + 两处约束替换，无存量改写；回滚：删 4 表并还原约束（先清理新 purpose 记录）。
PostgreSQL 与 SQLite 均可执行（约束替换用 batch_alter_table）。

Revision ID: d4f7b2c8e6a1
Revises: c7e1a5b9d3f2
Create Date: 2026-10-22 23:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d4f7b2c8e6a1"
down_revision = "c7e1a5b9d3f2"
branch_labels = None
depends_on = None

_CONSENT_PURPOSES_NEW = (
    "purpose IN ('package_purchase', 'steward_service', 'steward_device_data', "
    "'steward_phone_outreach', 'steward_contact_outreach', 'steward_call_recording')"
)
_CONSENT_PURPOSES_OLD = (
    "purpose IN ('package_purchase', 'steward_service', 'steward_device_data', "
    "'steward_phone_outreach', 'steward_contact_outreach')"
)
_TERMS_PURPOSES_NEW = (
    "purpose IN ('steward_service', 'steward_device_data', 'steward_phone_outreach', "
    "'steward_contact_outreach', 'steward_call_recording')"
)
_TERMS_PURPOSES_OLD = "purpose IN ('steward_service', 'steward_device_data')"


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
        "phone_scripts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(10), nullable=False),
        sa.Column("requires_recording", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("reviewed_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("review_note", sa.String(200), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["reviewed_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("code", "version", name="uq_phone_scripts_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired', 'rejected')", name="ck_phone_scripts_status"
        ),
        sa.CheckConstraint("target_type IN ('self', 'contact')", name="ck_phone_scripts_target"),
    )

    op.create_table(
        "phone_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("relation", sa.String(50), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("identity_note", sa.String(200), nullable=False),
        sa.Column("consent_status", sa.String(10), nullable=False, server_default="none"),
        sa.Column("consent_source", sa.String(200), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "consent_status IN ('none', 'granted', 'refused', 'revoked')",
            name="ck_phone_contacts_consent",
        ),
    )
    op.create_index("ix_phone_contacts_user", "phone_contacts", ["user_id", "id"])

    op.create_table(
        "phone_call_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(10), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("script_code", sa.String(32), nullable=False),
        sa.Column("script_version", sa.String(16), nullable=False),
        sa.Column("requires_recording", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(18), nullable=False, server_default="pending_review"),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(500), nullable=True),
        sa.Column("receipt_ref", sa.String(200), nullable=True),
        sa.Column("taken_over_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("taken_over_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["contact_id"], ["phone_contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["script_id"], ["phone_scripts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["taken_over_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('pending_review', 'queued', 'pending_config', 'in_progress', 'no_answer', "
            "'busy', 'wrong_number', 'refused', 'transferred_human', 'completed', 'failed', 'cancelled')",
            name="ck_phone_orders_status",
        ),
        sa.CheckConstraint("target_type IN ('self', 'contact')", name="ck_phone_orders_target"),
        sa.CheckConstraint("attempts >= 0", name="ck_phone_orders_attempts"),
    )
    op.create_index("ix_phone_orders_instance", "phone_call_orders", ["instance_id", "id"])
    op.create_index("ix_phone_orders_dispatch", "phone_call_orders", ["status", "next_retry_at"])

    op.create_table(
        "phone_call_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(8), nullable=False, server_default="system"),
        sa.Column("actor_admin_id", sa.Integer(), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("receipt_ref", sa.String(200), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["order_id"], ["phone_call_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "outcome IN ('no_answer', 'busy', 'wrong_number', 'refused', 'needs_human', 'completed', 'blocked')",
            name="ck_phone_attempts_outcome",
        ),
        sa.CheckConstraint("actor IN ('system', 'admin')", name="ck_phone_attempts_actor"),
    )
    op.create_index("ix_phone_attempts_order", "phone_call_attempts", ["order_id", "id"])

    # 授权 purpose 放宽（三类分立授权，E 启用）
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.drop_constraint("ck_mp_user_consents_purpose", type_="check")
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.create_check_constraint("ck_mp_user_consents_purpose", _CONSENT_PURPOSES_NEW)
    with op.batch_alter_table("steward_consent_terms") as batch_op:
        batch_op.drop_constraint("ck_steward_consent_terms_purpose", type_="check")
    with op.batch_alter_table("steward_consent_terms") as batch_op:
        batch_op.create_check_constraint("ck_steward_consent_terms_purpose", _TERMS_PURPOSES_NEW)


def downgrade() -> None:
    op.execute(
        "DELETE FROM steward_consent_terms WHERE purpose IN "
        "('steward_phone_outreach', 'steward_contact_outreach', 'steward_call_recording')"
    )
    op.execute(
        "DELETE FROM mp_user_consents WHERE purpose IN "
        "('steward_call_recording')"
    )
    with op.batch_alter_table("steward_consent_terms") as batch_op:
        batch_op.drop_constraint("ck_steward_consent_terms_purpose", type_="check")
    with op.batch_alter_table("steward_consent_terms") as batch_op:
        batch_op.create_check_constraint("ck_steward_consent_terms_purpose", _TERMS_PURPOSES_OLD)
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.drop_constraint("ck_mp_user_consents_purpose", type_="check")
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.create_check_constraint("ck_mp_user_consents_purpose", _CONSENT_PURPOSES_OLD)
    op.drop_index("ix_phone_attempts_order", table_name="phone_call_attempts")
    op.drop_table("phone_call_attempts")
    op.drop_index("ix_phone_orders_dispatch", table_name="phone_call_orders")
    op.drop_index("ix_phone_orders_instance", table_name="phone_call_orders")
    op.drop_table("phone_call_orders")
    op.drop_index("ix_phone_contacts_user", table_name="phone_contacts")
    op.drop_table("phone_contacts")
    op.drop_table("phone_scripts")
