"""Allow durable callbacks waiting for the dispatch response to establish correlation.

Revision ID: a8d3f5b7c9e1
Revises: f7a2c9e4b6d8
"""

from alembic import op
import sqlalchemy as sa

revision = "a8d3f5b7c9e1"
down_revision = "f7a2c9e4b6d8"
branch_labels = None
depends_on = None

OLD = "result IN ('applied', 'duplicate', 'stale', 'rejected_state')"
NEW = "result IN ('applied', 'duplicate', 'stale', 'rejected_state', 'pending_correlation')"


def upgrade():
    with op.batch_alter_table("phone_callback_events") as batch:
        batch.alter_column("result", existing_type=sa.String(16), type_=sa.String(24), existing_nullable=False)
        batch.drop_constraint("ck_phone_callback_events_result", type_="check")
        batch.create_check_constraint("ck_phone_callback_events_result", NEW)


def downgrade():
    # 旧程序无法处理待关联事件，保留拒绝记录，不能伪造 applied；回退前须对账。
    op.execute("UPDATE phone_callback_events SET result = 'rejected_state' WHERE result = 'pending_correlation'")
    with op.batch_alter_table("phone_callback_events") as batch:
        batch.drop_constraint("ck_phone_callback_events_result", type_="check")
        batch.create_check_constraint("ck_phone_callback_events_result", OLD)
        batch.alter_column("result", existing_type=sa.String(24), type_=sa.String(16), existing_nullable=False)
