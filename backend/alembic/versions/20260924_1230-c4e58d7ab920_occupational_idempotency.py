"""add client submission idempotency key without discarding preview records

Revision ID: c4e58d7ab920
Revises: 9e4b7f92b1d3
Create Date: 2026-09-24 12:30:00
"""

import uuid

from alembic import op
import sqlalchemy as sa

revision = "c4e58d7ab920"
down_revision = "9e4b7f92b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "occupational_submissions",
        sa.Column("client_submission_id", sa.String(36), nullable=True),
    )
    connection = op.get_bind()
    ids = connection.execute(sa.text("SELECT id FROM occupational_submissions")).scalars().all()
    for submission_id in ids:
        connection.execute(
            sa.text(
                "UPDATE occupational_submissions SET client_submission_id = :client_submission_id "
                "WHERE id = :submission_id"
            ),
            {"client_submission_id": str(uuid.uuid4()), "submission_id": submission_id},
        )
    with op.batch_alter_table("occupational_submissions") as batch:
        batch.alter_column("client_submission_id", existing_type=sa.String(36), nullable=False)
        batch.create_index("ux_occupational_client_submission_id", ["client_submission_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("occupational_submissions") as batch:
        batch.drop_index("ux_occupational_client_submission_id")
        batch.drop_column("client_submission_id")
