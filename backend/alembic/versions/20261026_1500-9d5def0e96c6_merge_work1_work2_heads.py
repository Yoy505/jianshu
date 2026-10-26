"""merge work1/work2 migration heads（工作一 × 工作二 双线合并）

工作一（交易与权益，本地线）与工作二（服务履约，GitHub 线）并行开发，
均从 e5c9a3f7b1d8 分叉，形成两个迁移头：
- 工作一头 a7d0e4f6b8c3（权益防重/开售放行/授权来源/consent 时间戳）
- 工作二头 e9a4c7f1b5d8（管家/触达/设备/专业工单/电话域 + 同源 consent 时间戳修复）

本迁移仅合并两头，无 schema 操作。两边对 mp_user_consents 时间戳列的
同源重复修复已在 a7d0e4f6b8c3 内做幂等处理（列存在即跳过）。

Revision ID: 9d5def0e96c6
Revises: a7d0e4f6b8c3, e9a4c7f1b5d8
Create Date: 2026-10-26 15:00:00
"""

revision = "9d5def0e96c6"
down_revision = ("a7d0e4f6b8c3", "e9a4c7f1b5d8")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
