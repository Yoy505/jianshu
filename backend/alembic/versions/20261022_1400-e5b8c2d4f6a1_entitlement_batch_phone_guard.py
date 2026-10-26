"""entitlement batch-phone guard and entitlement events (R5/R6)

R6 跨批次防重：mp_entitlements 增加 org_id（回填自批次），PostgreSQL 建部分唯一索引
uq_mp_entitlements_batch_phone（同单位×版本×手机号至多一份非 void 权益）；
SQLite 以等价 BEFORE 触发器实现（create_all 测试路径同款）。
R5 权益变更事件：新增 mp_entitlement_events（只追加），供工作二消费权益变化。

升级：新增列/索引/表并回填 org_id，不改写既有数据语义；
回滚：删触发器（SQLite）/索引（PG）、删事件表、删 org_id 列。

Revision ID: e5b8c2d4f6a1
Revises: e5c9a3f7b1d8
Create Date: 2026-10-22 14:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "e5b8c2d4f6a1"
down_revision = "e5c9a3f7b1d8"
branch_labels = None
depends_on = None


_ENT_GUARD_INSERT_SQL = """
CREATE TRIGGER IF NOT EXISTS mp_entitlements_batch_phone_guard
BEFORE INSERT ON mp_entitlements
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'duplicate batch entitlement phone')
    WHERE NEW.source_type = 'batch'
      AND NEW.status <> 'void'
      AND NEW.phone IS NOT NULL
      AND NEW.org_id IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM mp_entitlements
        WHERE source_type = 'batch'
          AND status <> 'void'
          AND phone = NEW.phone
          AND org_id = NEW.org_id
          AND package_version_id = NEW.package_version_id
          AND id != NEW.id
      );
END;
"""

_ENT_GUARD_UPDATE_SQL = """
CREATE TRIGGER IF NOT EXISTS mp_entitlements_batch_phone_guard_update
BEFORE UPDATE OF org_id, package_version_id, phone, status ON mp_entitlements
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'duplicate batch entitlement phone')
    WHERE NEW.source_type = 'batch'
      AND NEW.status <> 'void'
      AND NEW.phone IS NOT NULL
      AND NEW.org_id IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM mp_entitlements
        WHERE source_type = 'batch'
          AND status <> 'void'
          AND phone = NEW.phone
          AND org_id = NEW.org_id
          AND package_version_id = NEW.package_version_id
          AND id != NEW.id
      );
END;
"""


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    with op.batch_alter_table("mp_entitlements") as batch_op:
        batch_op.add_column(sa.Column("org_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_mp_entitlements_batch_phone",
        "mp_entitlements",
        ["org_id", "package_version_id", "phone"],
    )
    if not is_sqlite:
        op.create_foreign_key(
            "fk_mp_entitlements_org",
            "mp_entitlements",
            "mp_orgs",
            ["org_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    # 回填：批次来源权益的 org_id 取自所属批次
    op.execute(
        "UPDATE mp_entitlements SET org_id = ("
        "  SELECT b.org_id FROM mp_purchase_batches b WHERE b.id = mp_entitlements.batch_id)"
        " WHERE source_type = 'batch' AND batch_id IS NOT NULL AND org_id IS NULL"
    )
    if is_sqlite:
        op.execute(_ENT_GUARD_INSERT_SQL)
        op.execute(_ENT_GUARD_UPDATE_SQL)
    else:
        op.create_index(
            "uq_mp_entitlements_batch_phone",
            "mp_entitlements",
            ["org_id", "package_version_id", "phone"],
            unique=True,
            postgresql_where=sa.text(
                "source_type = 'batch' AND status <> 'void' AND phone IS NOT NULL"
            ),
        )

    op.create_table(
        "mp_entitlement_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entitlement_id",
            sa.Integer(),
            sa.ForeignKey("mp_entitlements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("actor_type", sa.String(10), nullable=False, server_default="system"),
        sa.Column("actor_id", sa.Integer(), nullable=True),
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
            "event_type IN ('issued', 'claimed', 'rejected', 'reclaimed', 'reassigned', 'voided')",
            name="ck_mp_entitlement_events_type",
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'admin', 'system')",
            name="ck_mp_entitlement_events_actor",
        ),
    )
    op.create_index(
        "ix_mp_entitlement_events_ent", "mp_entitlement_events", ["entitlement_id", "id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    op.drop_index("ix_mp_entitlement_events_ent", table_name="mp_entitlement_events")
    op.drop_table("mp_entitlement_events")
    if is_sqlite:
        op.execute("DROP TRIGGER IF EXISTS mp_entitlements_batch_phone_guard")
        op.execute("DROP TRIGGER IF EXISTS mp_entitlements_batch_phone_guard_update")
    else:
        op.drop_index("uq_mp_entitlements_batch_phone", table_name="mp_entitlements")
    op.drop_index("ix_mp_entitlements_batch_phone", table_name="mp_entitlements")
    if not is_sqlite:
        op.drop_constraint(
            "fk_mp_entitlements_org", "mp_entitlements", type_="foreignkey"
        )
    with op.batch_alter_table("mp_entitlements") as batch_op:
        batch_op.drop_column("org_id")
