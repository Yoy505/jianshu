"""volunteer applications for xinglin qianxing volunteer program

Revision ID: b2e6a9c4d117
Revises: a8d1f3c6b902
Create Date: 2026-10-16 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "b2e6a9c4d117"
down_revision = "a8d1f3c6b902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "volunteer_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receipt_no", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("city", sa.String(50), nullable=False),
        sa.Column("gender", sa.String(12), nullable=True),
        sa.Column("birth_year", sa.Integer(), nullable=True),
        sa.Column("occupation", sa.String(50), nullable=True),
        sa.Column("organization", sa.String(100), nullable=True),
        sa.Column("track", sa.String(10), nullable=False),
        sa.Column("offline_per_year", sa.Integer(), nullable=False),
        sa.Column("can_station", sa.Boolean(), nullable=False),
        sa.Column("intro", sa.Text(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("consent_text", sa.String(500), nullable=False),
        sa.Column(
            "consented_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("contact_ip", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'contacted', 'enrolled')", name="ck_volunteer_status"
        ),
        sa.CheckConstraint(
            "track IN ('practice', 'publicity', 'assist', 'promotion')",
            name="ck_volunteer_track",
        ),
        sa.CheckConstraint(
            "gender IS NULL OR gender IN ('male', 'female', 'undisclosed')",
            name="ck_volunteer_gender",
        ),
        sa.CheckConstraint(
            "offline_per_year >= 1 AND offline_per_year <= 99",
            name="ck_volunteer_offline_per_year",
        ),
        sa.CheckConstraint(
            "birth_year IS NULL OR (birth_year >= 1900 AND birth_year <= 2010)",
            name="ck_volunteer_birth_year",
        ),
    )
    op.create_index(
        "ix_volunteer_status_created", "volunteer_applications", ["status", "created_at"]
    )
    op.create_index("ux_volunteer_receipt_no", "volunteer_applications", ["receipt_no"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_volunteer_receipt_no", table_name="volunteer_applications")
    op.drop_index("ix_volunteer_status_created", table_name="volunteer_applications")
    op.drop_table("volunteer_applications")
