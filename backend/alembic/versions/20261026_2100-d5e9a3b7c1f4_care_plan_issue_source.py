"""care plan issue source & activation responsibility snapshot（C3：签发凭据与激活证据）

steward_care_plans 新增签发来源 issue_source（ops_entry 运营录入 / physician_self 医师本人
账号签发 / external_verified 外部签发材料经核验）、私有材料引用 evidence_ref、来源 evidence_source、
核验责任人 verified_by_admin_id / verified_at。只有可信来源（physician_self / external_verified）
的计划才能通过激活门禁与医学判断类工单的"无签发阻断"。

存量计划回填为 ops_entry（此前任何 steward.manage 账号都能替医师录入，无法事后证明来源）；
运营可通过 POST /care-plans/{id}/verify 补外部凭据升级为 external_verified。

steward_activation_credentials 新增 plan_id / plan_issue_source / responsibility_snapshot，
激活时固化真实计划、来源与责任人（录入人、核验人、各人工门禁完成人）。

Revision ID: d5e9a3b7c1f4
Revises: c4d8f2a6b9e3
Create Date: 2026-10-26 21:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "d5e9a3b7c1f4"
down_revision = "c4d8f2a6b9e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("steward_care_plans") as batch_op:
        batch_op.add_column(
            sa.Column("issue_source", sa.String(length=24), server_default="ops_entry", nullable=False)
        )
        batch_op.add_column(sa.Column("evidence_ref", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("evidence_source", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("verified_by_admin_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
        # SQLite 批量模式要求约束具名，故 FK 单独具名创建
        batch_op.create_foreign_key(
            "fk_steward_care_plans_verified_by_admin", "admin_users",
            ["verified_by_admin_id"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_steward_care_plans_issue_source",
            "issue_source IN ('ops_entry', 'physician_self', 'external_verified')",
        )
    with op.batch_alter_table("steward_activation_credentials") as batch_op:
        batch_op.add_column(sa.Column("plan_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_steward_activation_credentials_plan", "steward_care_plans",
            ["plan_id"], ["id"], ondelete="SET NULL",
        )
        batch_op.add_column(sa.Column("plan_issue_source", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("responsibility_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("steward_activation_credentials") as batch_op:
        batch_op.drop_column("responsibility_snapshot")
        batch_op.drop_column("plan_issue_source")
        batch_op.drop_constraint("fk_steward_activation_credentials_plan", type_="foreignkey")
        batch_op.drop_column("plan_id")
    with op.batch_alter_table("steward_care_plans") as batch_op:
        batch_op.drop_constraint("ck_steward_care_plans_issue_source", type_="check")
        batch_op.drop_constraint("fk_steward_care_plans_verified_by_admin", type_="foreignkey")
        batch_op.drop_column("verified_at")
        batch_op.drop_column("verified_by_admin_id")
        batch_op.drop_column("evidence_source")
        batch_op.drop_column("evidence_ref")
        batch_op.drop_column("issue_source")
