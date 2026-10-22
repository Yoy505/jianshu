"""steward service instances: fulfillment domain for work package 2

工作二（全域健康管家服务履约）首切片：服务实例、九项激活门禁、履约任务 / 日历、
暂停与补时、服务时间线、履约人员共 6 张表；同时放宽 mp_user_consents.purpose 约束
（新增 steward_service / steward_device_data / steward_phone_outreach / steward_contact_outreach）。

升级：全新增表 + 一处约束替换，无存量数据改写；回滚：按依赖逆序删表并还原约束。
PostgreSQL 与 SQLite 均可执行（约束替换用 batch_alter_table，SQLite 重建表）。

Revision ID: e5c9a3f7b1d8
Revises: d4a7b1c2e3f5
Create Date: 2026-10-22 10:30:00
"""

import sqlalchemy as sa
from alembic import op

revision = "e5c9a3f7b1d8"
down_revision = "d4a7b1c2e3f5"
branch_labels = None
depends_on = None

_CONSENT_PURPOSES_NEW = (
    "purpose IN ('package_purchase', 'steward_service', 'steward_device_data', "
    "'steward_phone_outreach', 'steward_contact_outreach')"
)
_CONSENT_PURPOSES_OLD = "purpose IN ('package_purchase')"


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
    # 授权 purpose 放宽：旧约束只允许 package_purchase，直接替换（无数据回填需求）
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.drop_constraint("ck_mp_user_consents_purpose", type_="check")
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.create_check_constraint("ck_mp_user_consents_purpose", _CONSENT_PURPOSES_NEW)

    op.create_table(
        "steward_staff",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("org_name", sa.String(100), nullable=True),
        sa.Column("title", sa.String(50), nullable=True),
        sa.Column("license_no", sa.String(50), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("note", sa.String(500), nullable=True),
        *_ts_columns(),
        sa.CheckConstraint("role IN ('butler', 'physician')", name="ck_steward_staff_role"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_steward_staff_status"),
    )

    op.create_table(
        "steward_service_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_no", sa.String(32), nullable=False),
        sa.Column("entitlement_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("package_version_id", sa.Integer(), nullable=False),
        sa.Column("package_name", sa.String(100), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("benefits", sa.JSON(), nullable=False),
        sa.Column("validity_days", sa.Integer(), nullable=False),
        sa.Column("service_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="preparing"),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("original_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("compensated_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calendar_generated", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("plan_version", sa.String(32), nullable=True),
        sa.Column("butler_id", sa.Integer(), nullable=True),
        sa.Column("backup_butler_id", sa.Integer(), nullable=True),
        sa.Column("physician_id", sa.Integer(), nullable=True),
        sa.Column("renewed_from_instance_id", sa.Integer(), nullable=True),
        sa.Column("termination_reason", sa.String(200), nullable=True),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closure_report", sa.Text(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["entitlement_id"], ["mp_entitlements.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["package_id"], ["mp_packages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["package_version_id"], ["mp_package_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["butler_id"], ["steward_staff.id"], ondelete="RESTRICT", name="fk_steward_instances_butler"
        ),
        sa.ForeignKeyConstraint(
            ["backup_butler_id"],
            ["steward_staff.id"],
            ondelete="RESTRICT",
            name="fk_steward_instances_backup_butler",
        ),
        sa.ForeignKeyConstraint(
            ["physician_id"], ["steward_staff.id"], ondelete="RESTRICT", name="fk_steward_instances_physician"
        ),
        sa.ForeignKeyConstraint(
            ["renewed_from_instance_id"],
            ["steward_service_instances.id"],
            ondelete="RESTRICT",
            name="fk_steward_instances_renewed_from",
        ),
        sa.UniqueConstraint("entitlement_id", name="uq_steward_instances_entitlement"),
        sa.CheckConstraint(
            "status IN ('preparing', 'active', 'paused', 'expired', 'closed', 'terminated')",
            name="ck_steward_instances_status",
        ),
        sa.CheckConstraint(
            "service_days >= 1 AND service_days <= 3650", name="ck_steward_instances_days"
        ),
        sa.CheckConstraint("compensated_seconds >= 0", name="ck_steward_instances_compensated"),
    )
    op.create_index("ix_steward_instances_user", "steward_service_instances", ["user_id", "id"])
    op.create_index(
        "ix_steward_instances_status", "steward_service_instances", ["status", "created_at"]
    )
    op.create_index("ix_steward_instances_butler", "steward_service_instances", ["butler_id"])

    op.create_table(
        "steward_prep_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("check_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(6), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="pending"),
        sa.Column("missing_reason", sa.String(200), nullable=True),
        sa.Column("evidence", sa.String(500), nullable=True),
        sa.Column("completed_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("instance_id", "check_type", name="uq_steward_prep_checks_type"),
        sa.CheckConstraint("status IN ('pending', 'passed')", name="ck_steward_prep_checks_status"),
        sa.CheckConstraint("source IN ('auto', 'manual')", name="ck_steward_prep_checks_source"),
    )

    op.create_table(
        "steward_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_key", sa.String(40), nullable=False),
        sa.Column("service_day", sa.Integer(), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("task_type", sa.String(12), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("assignee_type", sa.String(10), nullable=False),
        sa.Column("butler_id", sa.Integer(), nullable=True),
        sa.Column("physician_id", sa.Integer(), nullable=True),
        sa.Column("plan_version", sa.String(32), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(500), nullable=True),
        sa.Column("shifted_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["butler_id"], ["steward_staff.id"], ondelete="RESTRICT", name="fk_steward_tasks_butler"
        ),
        sa.ForeignKeyConstraint(
            ["physician_id"],
            ["steward_staff.id"],
            ondelete="RESTRICT",
            name="fk_steward_tasks_physician",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("instance_id", "task_key", name="uq_steward_tasks_key"),
        sa.CheckConstraint(
            "task_type IN ('daily', 'follow_up', 'review_prep', 'final_review', 'closure')",
            name="ck_steward_tasks_type",
        ),
        sa.CheckConstraint("status IN ('pending', 'done', 'cancelled')", name="ck_steward_tasks_status"),
        sa.CheckConstraint(
            "assignee_type IN ('user', 'butler', 'physician', 'system')",
            name="ck_steward_tasks_assignee",
        ),
        sa.CheckConstraint("service_day >= 1", name="ck_steward_tasks_day"),
    )
    op.create_index("ix_steward_tasks_instance_day", "steward_tasks", ["instance_id", "service_day"])
    op.create_index(
        "ix_steward_tasks_instance_date", "steward_tasks", ["instance_id", "service_date"]
    )
    op.create_index("ix_steward_tasks_user_date", "steward_tasks", ["user_id", "service_date"])

    op.create_table(
        "steward_pause_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("cause", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("compensated_seconds", sa.Integer(), nullable=True),
        sa.Column("tasks_shifted_days", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "cause IN ('provider_interruption', 'physician_advised', 'user_request', 'other')",
            name="ck_steward_pauses_cause",
        ),
    )
    op.create_index("ix_steward_pauses_instance", "steward_pause_records", ["instance_id", "id"])

    op.create_table(
        "steward_timeline_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("actor_type", sa.String(8), nullable=False, server_default="system"),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(100), nullable=False, server_default=""),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "actor_type IN ('user', 'admin', 'system')", name="ck_steward_timeline_actor"
        ),
    )
    op.create_index("ix_steward_timeline_instance", "steward_timeline_events", ["instance_id", "id"])
    op.create_index("ix_steward_timeline_user", "steward_timeline_events", ["user_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_steward_timeline_user", table_name="steward_timeline_events")
    op.drop_index("ix_steward_timeline_instance", table_name="steward_timeline_events")
    op.drop_table("steward_timeline_events")
    op.drop_index("ix_steward_pauses_instance", table_name="steward_pause_records")
    op.drop_table("steward_pause_records")
    op.drop_index("ix_steward_tasks_user_date", table_name="steward_tasks")
    op.drop_index("ix_steward_tasks_instance_date", table_name="steward_tasks")
    op.drop_index("ix_steward_tasks_instance_day", table_name="steward_tasks")
    op.drop_table("steward_tasks")
    op.drop_table("steward_prep_checks")
    op.drop_index("ix_steward_instances_butler", table_name="steward_service_instances")
    op.drop_index("ix_steward_instances_status", table_name="steward_service_instances")
    op.drop_index("ix_steward_instances_user", table_name="steward_service_instances")
    op.drop_table("steward_service_instances")
    op.drop_table("steward_staff")
    # 还原授权 purpose 约束（工作二新增的授权记录在回滚前须先清理）
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.drop_constraint("ck_mp_user_consents_purpose", type_="check")
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.create_check_constraint("ck_mp_user_consents_purpose", _CONSENT_PURPOSES_OLD)
