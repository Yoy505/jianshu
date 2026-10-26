"""consent timestamp columns (PG 集成发现的模型/迁移漂移修正)

T4 真实 PostgreSQL 集成测试暴露：mp_user_consents 模型带 TimestampMixin，
但初始迁移未创建 created_at/updated_at 列（SQLite create_all 测试路径掩盖了
差异，PG 按迁移建表即缺列）。实证对比（information_schema × 全模型）确认仅此
一处漂移；本迁移补齐该列，模型不变。

升级：仅新增带 server_default 的时间列；回滚：删除该列。无存量数据改写。

Revision ID: a7d0e4f6b8c3
Revises: f6c9d3e5a7b2
Create Date: 2026-10-22 20:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d0e4f6b8c3"
down_revision = "f6c9d3e5a7b2"
branch_labels = None
depends_on = None

# (表, 缺失列) —— information_schema 实证对比结果
_MISSING = {
    "mp_user_consents": ["created_at", "updated_at"],
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in _MISSING.items():
        # 幂等：工作二线 e9a4c7f1b5d8 独立修复了同一漂移（合并双线后可能先于本迁移执行），
        # 已存在的列不再重复添加，避免 duplicate column。
        existing = {col["name"] for col in inspector.get_columns(table)}
        missing = [c for c in columns if c not in existing]
        if not missing:
            continue
        with op.batch_alter_table(table) as batch_op:
            for column in missing:
                batch_op.add_column(
                    sa.Column(
                        column,
                        sa.DateTime(timezone=True),
                        server_default=sa.text("CURRENT_TIMESTAMP"),
                        nullable=False,
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in _MISSING.items():
        existing = {col["name"] for col in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch_op:
            for column in columns:
                if column in existing:
                    batch_op.drop_column(column)
