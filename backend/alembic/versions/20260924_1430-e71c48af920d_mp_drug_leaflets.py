"""Stage immutable drug leaflet originals outside public uploads.

Revision ID: e71c48af920d
Revises: b17e4c8d2a90
Create Date: 2026-09-24 14:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e71c48af920d"
down_revision = "b17e4c8d2a90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_drug_leaflets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("drug_id", sa.Integer(), sa.ForeignKey("mp_drugs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approval_no", sa.String(50), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_document_id", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("authorization_ref", sa.String(200), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("document_version", sa.String(100), nullable=True),
        sa.Column("source_checked_at", sa.Date(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("stored_filename", sa.String(68), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("drug_id", "file_sha256", name="uq_mp_drug_leaflets_drug_hash"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'published', 'withdrawn')",
            name="ck_mp_drug_leaflets_status",
        ),
    )
    op.create_index("ix_mp_drug_leaflets_drug_status", "mp_drug_leaflets", ["drug_id", "status"])
    op.create_index("ix_mp_drug_leaflets_approval_no", "mp_drug_leaflets", ["approval_no"])


def downgrade() -> None:
    op.drop_index("ix_mp_drug_leaflets_approval_no", table_name="mp_drug_leaflets")
    op.drop_index("ix_mp_drug_leaflets_drug_status", table_name="mp_drug_leaflets")
    op.drop_table("mp_drug_leaflets")
