"""Record preliminary PDF text identity check without endorsing legacy rows.

Revision ID: e72a93d8c411
Revises: e71c48af920d
Create Date: 2026-09-24 15:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e72a93d8c411"
down_revision = "e71c48af920d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mp_drug_leaflets",
        sa.Column("identity_check_method", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mp_drug_leaflets", "identity_check_method")
