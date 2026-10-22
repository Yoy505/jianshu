"""professional domain: care rules, staff-admin links, orders, events, reports, followups (slice D)

工作二切片 D（2026-10-22）：机构规则版本、账号↔人员绑定（防冒充）、专业工单与事件、
版本化服务报告、结构化随访记录；签发计划加 rules_version 列（引用机构规则版本）。

升级：全新增表 + 一列追加，无存量改写；回滚：删列删表。
PostgreSQL 与 SQLite 均可执行。

Revision ID: c7e1a5b9d3f2
Revises: b6d3f8a2c9e4
Create Date: 2026-10-22 21:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c7e1a5b9d3f2"
down_revision = "b6d3f8a2c9e4"
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
    with op.batch_alter_table("steward_care_plans") as batch_op:
        batch_op.add_column(sa.Column("rules_version", sa.String(32), nullable=True))

    op.create_table(
        "steward_care_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_name", sa.String(100), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="active"),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("org_name", "version", name="uq_steward_care_rules_version"),
        sa.CheckConstraint("status IN ('active', 'retired')", name="ck_steward_care_rules_status"),
    )

    op.create_table(
        "steward_staff_admin_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("staff_id", sa.Integer(), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["staff_id"], ["steward_staff.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["admin_id"], ["admin_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("staff_id", "admin_id", name="uq_steward_staff_admin_links"),
    )
    op.create_index("ix_steward_staff_admin_links_admin", "steward_staff_admin_links", ["admin_id"])

    op.create_table(
        "steward_professional_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("order_type", sa.String(24), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("detail", sa.String(1000), nullable=True),
        sa.Column("priority", sa.String(8), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("physician_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("care_plan_version", sa.String(32), nullable=True),
        sa.Column("rules_version", sa.String(32), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_note", sa.String(200), nullable=True),
        sa.Column("transferred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transfer_reason", sa.String(200), nullable=True),
        sa.Column("result", sa.String(1000), nullable=True),
        sa.Column("result_meta", sa.JSON(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["physician_id"], ["steward_staff.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["steward_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["completed_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "order_type IN ('initial_assessment', 'data_quality', 'physician_review', "
            "'final_review', 'exception')",
            name="ck_steward_prof_orders_type",
        ),
        sa.CheckConstraint("priority IN ('normal', 'high')", name="ck_steward_prof_orders_priority"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'done', 'cancelled')",
            name="ck_steward_prof_orders_status",
        ),
    )
    op.create_index("ix_steward_prof_orders_instance", "steward_professional_orders", ["instance_id", "id"])
    op.create_index(
        "ix_steward_prof_orders_open", "steward_professional_orders", ["status", "priority", "due_at"]
    )

    op.create_table(
        "steward_professional_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("actor_admin_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("detail", sa.JSON(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["order_id"], ["steward_professional_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "action IN ('created', 'started', 'transferred', 'escalated', 'completed', 'cancelled')",
            name="ck_steward_prof_events_action",
        ),
    )
    op.create_index("ix_steward_prof_events_order", "steward_professional_events", ["order_id", "id"])

    op.create_table(
        "steward_service_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("report_type", sa.String(10), nullable=False, server_default="closure"),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("data_summary", sa.JSON(), nullable=True),
        sa.Column("basis", sa.String(500), nullable=True),
        sa.Column("physician_id", sa.Integer(), nullable=True),
        sa.Column(
            "issued_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["physician_id"], ["steward_staff.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["steward_service_reports.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("instance_id", "version", name="uq_steward_service_reports_version"),
        sa.CheckConstraint("report_type IN ('closure', 'correction')", name="ck_steward_service_reports_type"),
        sa.CheckConstraint("version >= 1", name="ck_steward_service_reports_version_no"),
    )
    op.create_index("ix_steward_service_reports_instance", "steward_service_reports", ["instance_id", "id"])

    op.create_table(
        "steward_followup_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("service_day", sa.Integer(), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("data_quality_note", sa.String(500), nullable=True),
        sa.Column("next_action", sa.String(200), nullable=True),
        sa.Column("recorded_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["steward_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("task_id", name="uq_steward_followup_records_task"),
    )
    op.create_index(
        "ix_steward_followup_records_instance", "steward_followup_records", ["instance_id", "service_day"]
    )


def downgrade() -> None:
    op.drop_index("ix_steward_followup_records_instance", table_name="steward_followup_records")
    op.drop_table("steward_followup_records")
    op.drop_index("ix_steward_service_reports_instance", table_name="steward_service_reports")
    op.drop_table("steward_service_reports")
    op.drop_index("ix_steward_prof_events_order", table_name="steward_professional_events")
    op.drop_table("steward_professional_events")
    op.drop_index("ix_steward_prof_orders_open", table_name="steward_professional_orders")
    op.drop_index("ix_steward_prof_orders_instance", table_name="steward_professional_orders")
    op.drop_table("steward_professional_orders")
    op.drop_index("ix_steward_staff_admin_links_admin", table_name="steward_staff_admin_links")
    op.drop_table("steward_staff_admin_links")
    op.drop_table("steward_care_rules")
    with op.batch_alter_table("steward_care_plans") as batch_op:
        batch_op.drop_column("rules_version")
