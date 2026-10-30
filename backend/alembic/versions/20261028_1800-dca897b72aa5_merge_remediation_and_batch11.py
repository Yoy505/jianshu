"""merge heads（整改第二轮 × 工作二批次十一 双线合并）

两条线均从 9d5def0e96c6 分叉：
- 整改线头 a8d3f5b7c9e1（电话尝试绑定 f7a2c9e4b6d8 + 待关联回调事件）
- 批次十一头 b11f2a4c6d8e（随访预约意向表 steward_schedule_intents）

两域无表/列交集，本迁移仅合并两头，无 schema 操作。

Revision ID: dca897b72aa5
Revises: a8d3f5b7c9e1, b11f2a4c6d8e
Create Date: 2026-10-28 18:00:00
"""

revision = "dca897b72aa5"
down_revision = ("a8d3f5b7c9e1", "b11f2a4c6d8e")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
