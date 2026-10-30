"""phone callback events（F05：非终态回调幂等）

新增 phone_callback_events（供应商回调事件，只追加）：UNIQUE(order_id, event_key) 把同一事件的
串行 / 并发重复投递收敛为一次应用，result 保留 duplicate / stale / rejected_state 事实；
phone_call_orders 新增 external_request_id（真实 CCC 发起回执的在途请求号）。
回调处理改为"先持久化事件再按允许的原状态条件更新"，尝试次数只在拨打时计一次。

Revision ID: c4d8f2a6b9e3
Revises: b3c7e9d1a5f2
Create Date: 2026-10-26 20:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c4d8f2a6b9e3"
down_revision = "b3c7e9d1a5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phone_callback_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id", sa.Integer(),
            sa.ForeignKey("phone_call_orders.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=True),
        sa.Column("external_request_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("receipt_ref", sa.String(length=200), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.UniqueConstraint("order_id", "event_key", name="uq_phone_callback_events_key"),
        sa.CheckConstraint(
            "result IN ('applied', 'duplicate', 'stale', 'rejected_state')",
            name="ck_phone_callback_events_result",
        ),
    )
    op.create_index("ix_phone_callback_events_order", "phone_callback_events", ["order_id", "id"])
    with op.batch_alter_table("phone_call_orders") as batch_op:
        batch_op.add_column(sa.Column("external_request_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("phone_call_orders") as batch_op:
        batch_op.drop_column("external_request_id")
    op.drop_index("ix_phone_callback_events_order", table_name="phone_callback_events")
    op.drop_table("phone_callback_events")
