"""Create isolated private intake for unverified drug leaflet PDFs.

Revision ID: e74c5da19033
Revises: e73b4c9a2201
Create Date: 2026-09-24 17:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e74c5da19033"
down_revision = "e73b4c9a2201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_drug_leaflet_intake",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_namespace", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.String(200), nullable=False),
        sa.Column("source_acceptance_no", sa.String(100), nullable=True),
        sa.Column("source_product_name", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("source_publication_date", sa.Date(), nullable=True),
        sa.Column("claimed_approval_no", sa.String(100), nullable=True),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("version_note", sa.String(500), nullable=True),
        sa.Column("source_checked_at", sa.Date(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("stored_filename", sa.String(68), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("review_reasons", sa.JSON(), nullable=False),
        sa.Column("distribution_authorization_ref", sa.String(200), nullable=True),
        sa.Column("review_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "source_namespace", "source_record_id", "file_sha256",
            name="uq_mp_drug_leaflet_intake_source_hash",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'verified', 'rejected')",
            name="ck_mp_drug_leaflet_intake_review",
        ),
    )
    op.create_index(
        "ix_mp_drug_leaflet_intake_review", "mp_drug_leaflet_intake",
        ["review_status", "id"],
    )
    op.create_index(
        "ix_mp_drug_leaflet_intake_claimed_approval", "mp_drug_leaflet_intake",
        ["claimed_approval_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_mp_drug_leaflet_intake_claimed_approval", table_name="mp_drug_leaflet_intake")
    op.drop_index("ix_mp_drug_leaflet_intake_review", table_name="mp_drug_leaflet_intake")
    op.drop_table("mp_drug_leaflet_intake")
