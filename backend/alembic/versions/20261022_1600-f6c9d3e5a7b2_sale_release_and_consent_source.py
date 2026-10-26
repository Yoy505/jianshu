"""sale release gate and consent source (R7/R8/I1)

R8 开售放行：新增 mp_sale_releases（商品×版本×环境 + 负责人 + 依据 + 撤销留痕），
默认不放行；R7 授权管理：mp_user_consents 增加 source（授权来源，默认 order_confirm）。
环境放开销闸门后，无放行记录的服务包在 C 端与下单接口一致不可售。

升级：全新增表/列（source 带默认值，存量行回填 order_confirm），无破坏性改写；
回滚：删表/删列，不影响交易事实数据。

Revision ID: f6c9d3e5a7b2
Revises: e5b8c2d4f6a1
Create Date: 2026-10-22 16:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "f6c9d3e5a7b2"
down_revision = "e5b8c2d4f6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_sale_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "package_id",
            sa.Integer(),
            sa.ForeignKey("mp_packages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            sa.Integer(),
            sa.ForeignKey("mp_package_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("env", sa.String(10), nullable=False),
        sa.Column("basis", sa.String(500), nullable=False),
        sa.Column(
            "released_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoke_reason", sa.String(500), nullable=True),
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
            "env IN ('dev', 'test', 'staging', 'prod', 'all')", name="ck_mp_sale_releases_env"
        ),
    )
    op.create_index(
        "ix_mp_sale_releases_pkg", "mp_sale_releases", ["package_id", "version_id"]
    )
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(32), nullable=False, server_default="order_confirm")
        )
    with op.batch_alter_table("mp_purchase_batches") as batch_op:
        batch_op.add_column(sa.Column("stored_filename", sa.String(68), nullable=True))
        batch_op.add_column(sa.Column("normalized_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mp_purchase_batches") as batch_op:
        batch_op.drop_column("normalized_sha256")
        batch_op.drop_column("stored_filename")
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.drop_column("source")
    op.drop_index("ix_mp_sale_releases_pkg", table_name="mp_sale_releases")
    op.drop_table("mp_sale_releases")
