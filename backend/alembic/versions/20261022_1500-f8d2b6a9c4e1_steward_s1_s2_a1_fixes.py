"""steward s1/s2/a1 fixes: consent terms, pilot release, care plans, activation credential

工作二批次一（S1/S2/A1）：授权条款版本表、试点放行表、医师签发计划表、激活凭证表，
并为 steward_tasks 增加补录两列（actual_occurred_at / backfill_reason，A3 用）。
physician_plan 门禁由 manual 转 auto 属服务层判定变化，无存量数据改写：
已有 manual 置过的行在下次门禁读取时由服务端实时重判（快照回写），不需要数据迁移。

升级：全新增表 + 加列，无数据改写；回滚：删四表并删两列（先清引用再降级）。
PostgreSQL 与 SQLite 均可执行（加列 / 删列用 batch_alter_table）。

Revision ID: f8d2b6a9c4e1
Revises: e5c9a3f7b1d8
Create Date: 2026-10-22 15:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "f8d2b6a9c4e1"
down_revision = "e5c9a3f7b1d8"
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
        "steward_consent_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("status", sa.String(8), nullable=False, server_default="active"),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("purpose", "version", name="uq_steward_consent_terms_version"),
        sa.CheckConstraint(
            "purpose IN ('steward_service', 'steward_device_data')",
            name="ck_steward_consent_terms_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'retired')", name="ck_steward_consent_terms_status"
        ),
    )
    op.create_index(
        "ix_steward_consent_terms_purpose",
        "steward_consent_terms",
        ["purpose", "status", "id"],
    )

    op.create_table(
        "steward_pilot_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("package_version_id", sa.Integer(), nullable=False),
        sa.Column("env", sa.String(10), nullable=False),
        sa.Column("owner_name", sa.String(50), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(200), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["package_id"], ["mp_packages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["package_version_id"], ["mp_package_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("env IN ('dev', 'test', 'staging', 'prod')", name="ck_steward_releases_env"),
    )
    op.create_index(
        "ix_steward_releases_lookup",
        "steward_pilot_releases",
        ["package_version_id", "env", "revoked_at"],
    )

    op.create_table(
        "steward_care_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("instance_id", sa.Integer(), nullable=True),
        sa.Column("physician_id", sa.Integer(), nullable=False),
        sa.Column("org_name", sa.String(100), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(200), nullable=True),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=True),
        *_ts_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["steward_service_instances.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["physician_id"], ["steward_staff.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"], ["admin_users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("user_id", "version", name="uq_steward_care_plans_user_version"),
    )
    op.create_index("ix_steward_care_plans_user", "steward_care_plans", ["user_id", "id"])

    op.create_table(
        "steward_activation_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gate_snapshot", sa.JSON(), nullable=False),
        sa.Column("consent_versions", sa.JSON(), nullable=False),
        sa.Column("plan_version", sa.String(32), nullable=False),
        sa.Column("butler_id", sa.Integer(), nullable=False),
        sa.Column("backup_butler_id", sa.Integer(), nullable=False),
        sa.Column("physician_id", sa.Integer(), nullable=False),
        sa.Column("service_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_ends_at", sa.DateTime(timezone=True), nullable=False),
        *_ts_columns(),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["steward_service_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["butler_id"], ["steward_staff.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["backup_butler_id"], ["steward_staff.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["physician_id"], ["steward_staff.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("instance_id", name="uq_steward_activation_credentials_instance"),
    )

    with op.batch_alter_table("steward_tasks") as batch_op:
        batch_op.add_column(sa.Column("actual_occurred_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("backfill_reason", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("steward_tasks") as batch_op:
        batch_op.drop_column("backfill_reason")
        batch_op.drop_column("actual_occurred_at")
    op.drop_table("steward_activation_credentials")
    op.drop_index("ix_steward_care_plans_user", table_name="steward_care_plans")
    op.drop_table("steward_care_plans")
    op.drop_index("ix_steward_releases_lookup", table_name="steward_pilot_releases")
    op.drop_table("steward_pilot_releases")
    op.drop_index("ix_steward_consent_terms_purpose", table_name="steward_consent_terms")
    op.drop_table("steward_consent_terms")
