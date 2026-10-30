"""consent timestamp columns (PG 集成发现的模型/迁移漂移修正)

T4 真实 PostgreSQL 集成测试暴露：mp_user_consents 模型带 TimestampMixin，
但初始迁移未创建 created_at/updated_at 列（SQLite create_all 测试路径掩盖了
差异，PG 按迁移建表即缺列）。实证对比（information_schema × 全模型）确认仅此
一处漂移；本迁移补齐该列，模型不变。

升级：仅新增缺失的带 server_default 时间列；回滚：删除该列。无存量数据改写。

历史兼容修补（2026-10-26，F01）：工作二线 e9a4c7f1b5d8 对同一组列做了同源修复，
双线合并（9d5def0e96c6）后两条线会先后执行。升级侧原已幂等；回退侧原实现
"列存在即删"，当另一条线仍生效时会把它需要的列删掉，随后 e9a4c7f1b5d8 回退
再删即失败（PG UndefinedColumn / SQLite KeyError）。现改为"列归属感知"：
另一条线的同源修订仍在已应用修订之内时保留列，否则删除存在的列。

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
# 工作二线对同一组列的同源修复；它仍生效时本修订回退不得删列
_SIBLING_REVISION = "e9a4c7f1b5d8"


def _existing_columns(table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def _sibling_still_applied() -> bool:
    ctx = op.get_context()
    heads = ctx.get_current_heads()
    script = getattr(ctx, "script", None)
    if script is None:
        return _SIBLING_REVISION in heads
    return any(
        rev.revision == _SIBLING_REVISION
        for head in heads
        for rev in script.iterate_revisions(head, "base")
    )


def upgrade() -> None:
    for table, columns in _MISSING.items():
        # 幂等：工作二线 e9a4c7f1b5d8 独立修复了同一漂移（合并双线后可能先于本迁移执行），
        # 已存在的列不再重复添加，避免 duplicate column。
        existing = _existing_columns(table)
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
    if _sibling_still_applied():
        return
    for table, columns in _MISSING.items():
        present = [c for c in reversed(columns) if c in _existing_columns(table)]
        if not present:
            continue
        with op.batch_alter_table(table) as batch_op:
            for column in present:
                batch_op.drop_column(column)
