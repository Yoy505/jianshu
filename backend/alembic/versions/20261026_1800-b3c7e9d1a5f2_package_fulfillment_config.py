"""package fulfillment config（F03：商品版本履约配置，禁止统一套用 45 天睡眠流程）

商品版本新增履约三字段：fulfillment_type（none=普通商品，只发权益不建实例 /
steward=进入管家履约）、service_days（实际服务期，与权益 validity_days 独立）、
fulfillment_config（服务模板版本与任务配置 JSON）。服务实例新增 fulfillment_config
快照列：建实例时从商品版本复制，历史实例按购买时快照解释。

数据回填（保留已售事实）：本迁移前创建的全部版本，权益一律自动生成管家实例、
服务期一律取全局 STEWARD_SERVICE_DAYS（默认 45）——即事实上按「steward / 45 天」
售出。回填把这些事实固化到行内，此后全局参数只作为建版本时的受控默认值，
不再在建实例时读取。

Revision ID: b3c7e9d1a5f2
Revises: 9d5def0e96c6
Create Date: 2026-10-26 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "b3c7e9d1a5f2"
down_revision = "9d5def0e96c6"
branch_labels = None
depends_on = None

# 迁移前全局 STEWARD_SERVICE_DAYS 的默认值（已售事实，不随环境配置改变）
_HISTORIC_SERVICE_DAYS = 45


def upgrade() -> None:
    with op.batch_alter_table("mp_package_versions") as batch_op:
        batch_op.add_column(
            sa.Column("fulfillment_type", sa.String(length=16), server_default="none", nullable=False)
        )
        batch_op.add_column(sa.Column("service_days", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("fulfillment_config", sa.JSON(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE mp_package_versions "
            "SET fulfillment_type = 'steward', service_days = :days"
        ).bindparams(days=_HISTORIC_SERVICE_DAYS)
    )
    with op.batch_alter_table("mp_package_versions") as batch_op:
        batch_op.create_check_constraint(
            "ck_mp_package_versions_fulfillment_type",
            "fulfillment_type IN ('none', 'steward')",
        )
        batch_op.create_check_constraint(
            "ck_mp_package_versions_service_days",
            "(fulfillment_type = 'steward' AND service_days >= 1 AND service_days <= 365) "
            "OR (fulfillment_type <> 'steward' AND service_days IS NULL)",
        )
    with op.batch_alter_table("steward_service_instances") as batch_op:
        batch_op.add_column(sa.Column("fulfillment_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("steward_service_instances") as batch_op:
        batch_op.drop_column("fulfillment_config")
    with op.batch_alter_table("mp_package_versions") as batch_op:
        batch_op.drop_constraint("ck_mp_package_versions_service_days", type_="check")
        batch_op.drop_constraint("ck_mp_package_versions_fulfillment_type", type_="check")
        batch_op.drop_column("fulfillment_config")
        batch_op.drop_column("service_days")
        batch_op.drop_column("fulfillment_type")
