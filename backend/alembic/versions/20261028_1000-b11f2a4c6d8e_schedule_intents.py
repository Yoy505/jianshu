"""steward schedule intents (batch 11): user-side follow-up schedule intents

Revision ID: b11f2a4c6d8e
Revises: 9d5def0e96c6
Create Date: 2026-10-28

- 新表 steward_schedule_intents：随访预约意向（意向非确认，同任务旧 pending 置 superseded）
- 无存量数据改写；回滚 drop 表即可
"""
from alembic import op
import sqlalchemy as sa

revision = "b11f2a4c6d8e"
down_revision = "9d5def0e96c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "steward_schedule_intents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("steward_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "instance_id",
            sa.Integer(),
            sa.ForeignKey("steward_service_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("preferred_date", sa.Date(), nullable=False),
        sa.Column("preferred_period", sa.String(4), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column(
            "status", sa.String(12), nullable=False, server_default="pending"
        ),
        sa.Column(
            "handled_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handle_note", sa.String(200), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'handled', 'superseded')",
            name="ck_steward_schedule_intents_status",
        ),
        sa.CheckConstraint(
            "preferred_period IN ('上午', '下午', '晚间')",
            name="ck_steward_schedule_intents_period",
        ),
    )
    op.create_index(
        "ix_steward_schedule_intents_instance", "steward_schedule_intents", ["instance_id", "status"]
    )
    op.create_index(
        "ix_steward_schedule_intents_status", "steward_schedule_intents", ["status", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_steward_schedule_intents_status", table_name="steward_schedule_intents")
    op.drop_index("ix_steward_schedule_intents_instance", table_name="steward_schedule_intents")
    op.drop_table("steward_schedule_intents")
