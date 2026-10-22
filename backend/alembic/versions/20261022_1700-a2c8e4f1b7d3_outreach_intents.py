"""outreach domain: channel-agnostic outreach intents, attempts and subscriptions (slice B)

工作二切片 B（2026-10-22）：触达意图（幂等键 UNIQUE(instance_id, purpose, service_date)）、
只追加尝试记录、user 级退订状态。无任何发送能力与回执字段（通道未决策，边界见契约）。

升级：全新增表，无存量改写；回滚：按依赖逆序删三表。
PostgreSQL 与 SQLite 均可执行。

Revision ID: a2c8e4f1b7d3
Revises: f8d2b6a9c4e1
Create Date: 2026-10-22 17:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "a2c8e4f1b7d3"
down_revision = "f8d2b6a9c4e1"
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
        "outreach_intents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("content_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending_channel"),
        sa.Column("contactable", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("contact_note", sa.String(200), nullable=True),
        sa.Column("assignee_staff_id", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stop_reason", sa.String(32), nullable=True),
        sa.Column("channel_receipt_ref", sa.String(200), nullable=True),
        sa.Column("result", sa.String(500), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["steward_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assignee_staff_id"], ["steward_staff.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("instance_id", "purpose", "service_date", name="uq_outreach_intents_key"),
        sa.CheckConstraint(
            "status IN ('pending_channel', 'pending_manual', 'pending_confirm', "
            "'resolved', 'failed', 'cancelled')",
            name="ck_outreach_intents_status",
        ),
        sa.CheckConstraint(
            "purpose IN ('daily_reminder', 'follow_up')", name="ck_outreach_intents_purpose"
        ),
        sa.CheckConstraint(
            "stop_reason IN ('instance_expired', 'instance_terminated', 'service_paused', "
            "'unsubscribed', 'consent_revoked', 'manual_cancel') OR stop_reason IS NULL",
            name="ck_outreach_intents_stop_reason",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_outreach_intents_attempts"),
    )
    op.create_index(
        "ix_outreach_intents_status_date", "outreach_intents", ["status", "service_date"]
    )
    op.create_index(
        "ix_outreach_intents_assignee", "outreach_intents", ["assignee_staff_id", "status"]
    )
    op.create_index("ix_outreach_intents_user", "outreach_intents", ["user_id", "id"])

    op.create_table(
        "outreach_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("intent_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_type", sa.String(8), nullable=False, server_default="system"),
        sa.Column("actor_admin_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("contact_method", sa.String(10), nullable=True),
        sa.Column("outcome", sa.String(500), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["intent_id"], ["outreach_intents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["actor_admin_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "action IN ('generated', 'routed_manual', 'assigned', 'released', 'submitted', "
            "'confirmed', 'rejected', 'cancelled', 'stopped', 'reassigned')",
            name="ck_outreach_attempts_action",
        ),
        sa.CheckConstraint(
            "contact_method IN ('phone', 'chat', 'other') OR contact_method IS NULL",
            name="ck_outreach_attempts_contact_method",
        ),
        sa.CheckConstraint(
            "actor_type IN ('admin', 'system')", name="ck_outreach_attempts_actor"
        ),
    )
    op.create_index("ix_outreach_attempts_intent", "outreach_attempts", ["intent_id", "id"])

    op.create_table(
        "outreach_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subscribed", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(16), nullable=False, server_default="user"),
        sa.Column("note", sa.String(200), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_outreach_subscriptions_user"),
    )
    op.create_index(
        "ix_outreach_subscriptions_user", "outreach_subscriptions", ["user_id", "subscribed"]
    )


def downgrade() -> None:
    op.drop_index("ix_outreach_subscriptions_user", table_name="outreach_subscriptions")
    op.drop_table("outreach_subscriptions")
    op.drop_index("ix_outreach_attempts_intent", table_name="outreach_attempts")
    op.drop_table("outreach_attempts")
    op.drop_index("ix_outreach_intents_user", table_name="outreach_intents")
    op.drop_index("ix_outreach_intents_assignee", table_name="outreach_intents")
    op.drop_index("ix_outreach_intents_status_date", table_name="outreach_intents")
    op.drop_table("outreach_intents")
