"""volunteer track other + free note, drop gender collection

Revision ID: c3f8d2e5a928
Revises: b2e6a9c4d117
Create Date: 2026-10-16 19:30:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c3f8d2e5a928"
down_revision = "b2e6a9c4d117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("volunteer_applications") as batch:
        batch.add_column(sa.Column("track_note", sa.String(50), nullable=True))
        batch.drop_constraint("ck_volunteer_track", type_="check")
        batch.create_check_constraint(
            "ck_volunteer_track",
            "track IN ('practice', 'publicity', 'assist', 'promotion', 'other')",
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE volunteer_applications SET track = 'practice' "
            "WHERE track = 'other'"
        )
    )
    with op.batch_alter_table("volunteer_applications") as batch:
        batch.drop_constraint("ck_volunteer_track", type_="check")
        batch.create_check_constraint(
            "ck_volunteer_track",
            "track IN ('practice', 'publicity', 'assist', 'promotion')",
        )
        batch.drop_column("track_note")
