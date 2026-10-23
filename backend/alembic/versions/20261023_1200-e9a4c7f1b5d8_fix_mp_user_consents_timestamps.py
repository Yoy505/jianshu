"""fix: add missing timestamp columns on mp_user_consents (model/migration drift)

工作一迁移 d4a7b1c2e3f5 建 mp_user_consents 时遗漏 ORM TimestampMixin 的
created_at / updated_at 两列——create_all 测试库不暴露，alembic 部署库会因
缺列报错（小程序联调实测发现）。本迁移补齐两列，存量行以当前时间填充。

Revision ID: e9a4c7f1b5d8
Revises: d4f7b2c8e6a1
Create Date: 2026-10-23 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "e9a4c7f1b5d8"
down_revision = "d4f7b2c8e6a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("mp_user_consents") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
