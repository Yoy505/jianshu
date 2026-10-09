"""occupational health public submissions

Revision ID: 9e4b7f92b1d3
Revises: a1c4f7e2b9d8
Create Date: 2026-09-24 11:30:00
"""

from alembic import op
import sqlalchemy as sa

revision = "9e4b7f92b1d3"
down_revision = "a1c4f7e2b9d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "occupational_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receipt_no", sa.String(32), nullable=False, unique=True),
        sa.Column("work_type", sa.String(10), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("author_name", sa.String(50), nullable=False),
        sa.Column("organization", sa.String(100), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_review_contact", sa.Boolean(), nullable=False),
        sa.Column("consent_text", sa.String(500), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contact_ip", sa.String(64), nullable=True),
        sa.Column("stored_filename", sa.String(80), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("video_width", sa.Integer(), nullable=True),
        sa.Column("video_height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("work_type IN ('article', 'video')", name="ck_occupational_work_type"),
        sa.CheckConstraint(
            "status IN ('submitted', 'reviewing', 'accepted', 'rejected')",
            name="ck_occupational_status",
        ),
    )
    op.create_index(
        "ix_occupational_status_created", "occupational_submissions", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_occupational_status_created", table_name="occupational_submissions")
    op.drop_table("occupational_submissions")
