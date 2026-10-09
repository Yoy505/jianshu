"""Add private regulatory-source staging without changing existing drugs.

Revision ID: e73b4c9a2201
Revises: e72a93d8c411
Create Date: 2026-09-24 16:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e73b4c9a2201"
down_revision = "e72a93d8c411"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_drug_regulatory_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_namespace", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.String(200), nullable=False),
        sa.Column("registration_identifier", sa.String(100), nullable=True),
        sa.Column("source_product_name", sa.String(200), nullable=False),
        sa.Column("drug_class", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("regulatory_status", sa.String(24), nullable=False),
        sa.Column("source_status_text", sa.String(200), nullable=True),
        sa.Column("regulatory_as_of", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("source_document_name", sa.String(255), nullable=False),
        sa.Column("source_document_sha256", sa.String(64), nullable=False),
        sa.Column("stored_document_key", sa.String(69), nullable=False),
        sa.Column("source_checked_at", sa.Date(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column(
            "canonical_drug_id", sa.Integer(),
            sa.ForeignKey("mp_drugs.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("review_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "source_namespace", "source_record_id",
            name="uq_mp_drug_regulatory_sources_source_key",
        ),
        sa.CheckConstraint(
            "drug_class IN ('chemical', 'tcm', 'biologic')",
            name="ck_mp_drug_regulatory_sources_class",
        ),
        sa.CheckConstraint(
            "origin IN ('domestic', 'imported')",
            name="ck_mp_drug_regulatory_sources_origin",
        ),
        sa.CheckConstraint(
            "regulatory_status IN ('approved_current', 'revoked', 'expired', 'suspended', 'unknown')",
            name="ck_mp_drug_regulatory_sources_status",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'accepted', 'rejected')",
            name="ck_mp_drug_regulatory_sources_review",
        ),
    )
    op.create_index(
        "ix_mp_drug_regulatory_sources_identifier",
        "mp_drug_regulatory_sources", ["registration_identifier"],
    )
    op.create_index(
        "ix_mp_drug_regulatory_sources_class_origin",
        "mp_drug_regulatory_sources", ["drug_class", "origin"],
    )
    op.create_table(
        "mp_drug_regulatory_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id", sa.Integer(),
            sa.ForeignKey("mp_drug_regulatory_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_document_sha256", sa.String(64), nullable=False),
        sa.Column("stored_document_key", sa.String(69), nullable=False),
        sa.Column("source_document_name", sa.String(255), nullable=False),
        sa.Column("source_url", sa.String(1000), nullable=False),
        sa.Column("fact_sha256", sa.String(64), nullable=False),
        sa.Column("reported_facts", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("source_checked_at", sa.Date(), nullable=False),
        sa.Column("review_status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "source_id", "source_document_sha256", "source_url", "fact_sha256",
            name="uq_mp_drug_regulatory_observations_evidence",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'accepted', 'rejected')",
            name="ck_mp_drug_regulatory_observations_review",
        ),
        sa.CheckConstraint(
            "kind IN ('matching', 'conflict')",
            name="ck_mp_drug_regulatory_observations_kind",
        ),
    )
    op.create_index(
        "ix_mp_drug_regulatory_observations_hash",
        "mp_drug_regulatory_observations", ["source_document_sha256"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mp_drug_regulatory_observations_hash",
        table_name="mp_drug_regulatory_observations",
    )
    op.drop_table("mp_drug_regulatory_observations")
    op.drop_index(
        "ix_mp_drug_regulatory_sources_class_origin",
        table_name="mp_drug_regulatory_sources",
    )
    op.drop_index(
        "ix_mp_drug_regulatory_sources_identifier",
        table_name="mp_drug_regulatory_sources",
    )
    op.drop_table("mp_drug_regulatory_sources")
