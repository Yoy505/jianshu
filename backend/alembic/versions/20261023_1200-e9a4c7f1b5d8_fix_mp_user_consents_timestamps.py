"""fix: add missing timestamp columns on mp_user_consents (model/migration drift)

工作一迁移 d4a7b1c2e3f5 建 mp_user_consents 时遗漏 ORM TimestampMixin 的
created_at / updated_at 两列——create_all 测试库不暴露，alembic 部署库会因
缺列报错（小程序联调实测发现）。本迁移补齐两列，存量行以当前时间填充。

历史兼容修补（2026-10-26，F01）：工作一线 a7d0e4f6b8c3 对同一组列做了同源修复，
双线合并（9d5def0e96c6）后本修订可能在它之后执行。原实现无条件 add/drop：
- 升级：库处于工作一旧头 a7d0e4f6b8c3 时再升到合并头，PG 报 DuplicateColumn、
  SQLite 批量模式报 CircularDependencyError；
- 回退：a7d0e4f6b8c3 先删列、本修订再删，PG 报 UndefinedColumn、SQLite 报 KeyError。
现改为"列归属感知"：升级只补缺失列；回退时若另一条线的同源修订仍在当前已应用
修订之内，这组列仍被需要，保留不删，否则删除存在的列。已升级到本修订之后的库
不受影响（列已存在 → 升级无操作）。失败发生在历史修订内部，末尾新增迁移无法
拦截，也不以 stamp 伪装执行成功，故在原修订上修补。

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

_TABLE = "mp_user_consents"
_COLUMNS = ("created_at", "updated_at")
# 工作一线对同一组列的同源修复；它仍生效时本修订回退不得删列
_SIBLING_REVISION = "a7d0e4f6b8c3"


def _existing_columns() -> set[str]:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(_TABLE)}


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
    missing = [c for c in _COLUMNS if c not in _existing_columns()]
    if not missing:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
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
    present = [c for c in reversed(_COLUMNS) if c in _existing_columns()]
    if not present:
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        for column in present:
            batch_op.drop_column(column)
