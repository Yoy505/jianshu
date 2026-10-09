"""occupational two-stage review: statuses, soft lock, review records

Revision ID: a8d1f3c6b902
Revises: e74c5da19033
Create Date: 2026-10-08 10:30:00
"""

from alembic import op
import sqlalchemy as sa

revision = "a8d1f3c6b902"
down_revision = "e74c5da19033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()

    # 1) 旧单级 reviewing 改写为 initial_reviewing（既有 accepted/rejected 保持原状）
    connection.execute(
        sa.text(
            "UPDATE occupational_submissions SET status = 'initial_reviewing' "
            "WHERE status = 'reviewing'"
        )
    )

    # 2) 状态约束换为两级评审五态；同时加初审软锁列（SQLite 需 batch 重建表，
    #    FK 单独一批，避免同一 batch 内新增列间出现复制顺序的循环依赖）
    with op.batch_alter_table("occupational_submissions") as batch:
        batch.drop_constraint("ck_occupational_status", type_="check")
        batch.create_check_constraint(
            "ck_occupational_status",
            "status IN ('submitted', 'initial_reviewing', 'pending_final', 'accepted', 'rejected')",
        )
        batch.add_column(sa.Column("locked_by_admin_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("occupational_submissions") as batch:
        batch.create_foreign_key(
            "fk_occ_locked_by_admin",
            "admin_users",
            ["locked_by_admin_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 3) 分级评审记录表（每投稿每阶段至多一条结论）
    op.create_table(
        "occupational_review_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("occupational_submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("stage", sa.String(10), nullable=False),
        sa.Column("reviewer_name", sa.String(50), nullable=False),
        sa.Column("conclusion", sa.String(10), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
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
        sa.CheckConstraint("stage IN ('initial', 'final')", name="ck_occupational_review_stage"),
        sa.CheckConstraint(
            "conclusion IN ('passed', 'rejected')", name="ck_occupational_review_conclusion"
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)", name="ck_occupational_review_score"
        ),
        sa.UniqueConstraint("submission_id", "stage", name="uq_occupational_review_submission_stage"),
    )

    # 4) 既有终态记录补写 stage=final 评审留痕（历史单级审核结论视作终审）
    connection.execute(
        sa.text(
            "INSERT INTO occupational_review_records "
            "  (submission_id, stage, reviewer_id, reviewer_name, conclusion, comment, score, created_at, updated_at) "
            "SELECT s.id, 'final', "
            "  COALESCE(s.reviewed_by, (SELECT MIN(id) FROM admin_users)), "
            "  COALESCE(a.name, '未知管理员'), "
            "  CASE WHEN s.status = 'accepted' THEN 'passed' ELSE 'rejected' END, "
            "  s.review_comment, NULL, "
            "  COALESCE(s.reviewed_at, s.updated_at), COALESCE(s.reviewed_at, s.updated_at) "
            "FROM occupational_submissions s "
            "LEFT JOIN admin_users a ON a.id = s.reviewed_by "
            "WHERE s.status IN ('accepted', 'rejected') AND s.reviewed_at IS NOT NULL"
        )
    )

    # 4) 软锁引用的历史数据无需回填（旧流程无锁概念，列保持 NULL）


def downgrade() -> None:
    connection = op.get_bind()
    op.drop_table("occupational_review_records")
    with op.batch_alter_table("occupational_submissions") as batch:
        batch.drop_constraint("fk_occ_locked_by_admin", type_="foreignkey")
    with op.batch_alter_table("occupational_submissions") as batch:
        batch.drop_column("locked_at")
        batch.drop_column("locked_by_admin_id")
        batch.drop_constraint("ck_occupational_status", type_="check")
        batch.create_check_constraint(
            "ck_occupational_status",
            "status IN ('submitted', 'reviewing', 'accepted', 'rejected')",
        )
    connection.execute(
        sa.text(
            "UPDATE occupational_submissions SET status = 'reviewing' "
            "WHERE status IN ('initial_reviewing', 'pending_final')"
        )
    )
