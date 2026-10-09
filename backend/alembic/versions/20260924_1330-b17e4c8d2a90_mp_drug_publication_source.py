"""Add drug source metadata and fail-closed publication status.

Revision ID: b17e4c8d2a90
Revises: c4e58d7ab920
Create Date: 2026-09-24 13:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "b17e4c8d2a90"
down_revision = "c4e58d7ab920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mp_drugs", sa.Column("approval_no", sa.String(50), nullable=True))
    op.add_column("mp_drugs", sa.Column("specification", sa.String(100), nullable=True))
    op.add_column("mp_drugs", sa.Column("license_holder", sa.String(200), nullable=True))
    op.add_column("mp_drugs", sa.Column("source_name", sa.String(200), nullable=True))
    op.add_column("mp_drugs", sa.Column("source_url", sa.String(1000), nullable=True))
    op.add_column("mp_drugs", sa.Column("source_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "mp_drugs",
        sa.Column("publication_status", sa.String(16), server_default="pending", nullable=False),
    )
    # Every pre-existing row is legacy demo data, even when it contains a real drug name.
    # Never let the schema default silently promote it to a public record.
    op.execute(sa.text("UPDATE mp_drugs SET publication_status = 'demo'"))
    with op.batch_alter_table("mp_drugs") as batch:
        batch.create_unique_constraint("uq_mp_drugs_approval_no", ["approval_no"])
        batch.create_check_constraint(
            "ck_mp_drugs_publication_status",
            "publication_status IN ('demo', 'pending', 'published', 'offline')",
        )


def downgrade() -> None:
    with op.batch_alter_table("mp_drugs") as batch:
        batch.drop_constraint("ck_mp_drugs_publication_status", type_="check")
        batch.drop_constraint("uq_mp_drugs_approval_no", type_="unique")
        batch.drop_column("publication_status")
        batch.drop_column("source_checked_at")
        batch.drop_column("source_url")
        batch.drop_column("source_name")
        batch.drop_column("license_holder")
        batch.drop_column("specification")
        batch.drop_column("approval_no")
