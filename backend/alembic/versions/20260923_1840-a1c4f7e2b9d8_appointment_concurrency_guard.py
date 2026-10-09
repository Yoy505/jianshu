"""appointment concurrency guard: partial unique index + sqlite trigger

Revision ID: a1c4f7e2b9d8
Revises: 6c7ac92fcaa0
Create Date: 2026-09-23 18:40:00.000000

同一用户同日同时段最多一条进行中预约，由数据库层保证（审核报告 P2「先查后写」订正）：
- PostgreSQL：部分唯一索引（仅 status IN ('pending','confirmed') 的行参与唯一约束）
- SQLite（测试环境）：等价的 BEFORE INSERT/UPDATE 触发器（SQLite 不支持部分索引的
  唯一约束语义按此实现——实际是支持部分唯一索引，但测试库经 create_all 建表，
  迁移只在 PG 执行；触发器用于 create_all 路径的等价保护）

回滚：删除索引/触发器后恢复为先查后写的应用层防重（仍可用）。
"""

from alembic import op
from sqlalchemy import text

revision = "a1c4f7e2b9d8"
down_revision = "6c7ac92fcaa0"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_mp_appointments_active_slot"
TRIGGER_NAME = "mp_appointments_active_slot_guard"


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.create_index(
            INDEX_NAME,
            "mp_appointments",
            ["user_id", "appt_date", "period"],
            unique=True,
            postgresql_where=text("status IN ('pending', 'confirmed')"),
        )
    else:
        # SQLite 及其他方言：BEFORE 触发器等价保护（RAISE ABORT 映射 IntegrityError）
        op.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {TRIGGER_NAME}
            BEFORE INSERT ON mp_appointments
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, 'duplicate active appointment slot')
                WHERE NEW.status IN ('pending','confirmed')
                  AND EXISTS (
                    SELECT 1 FROM mp_appointments
                    WHERE user_id = NEW.user_id
                      AND appt_date = NEW.appt_date
                      AND period = NEW.period
                      AND status IN ('pending','confirmed')
                      AND id != NEW.id
                  );
            END;
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {TRIGGER_NAME}_update
            BEFORE UPDATE OF user_id, appt_date, period, status ON mp_appointments
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, 'duplicate active appointment slot')
                WHERE NEW.status IN ('pending','confirmed')
                  AND EXISTS (
                    SELECT 1 FROM mp_appointments
                    WHERE user_id = NEW.user_id
                      AND appt_date = NEW.appt_date
                      AND period = NEW.period
                      AND status IN ('pending','confirmed')
                      AND id != NEW.id
                  );
            END;
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.drop_index(INDEX_NAME, table_name="mp_appointments")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME};")
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}_update;")
