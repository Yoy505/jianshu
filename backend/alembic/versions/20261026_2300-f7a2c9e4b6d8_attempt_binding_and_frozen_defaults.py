"""Persist dial correlation and freeze missing fulfillment defaults.

Legacy attempts cannot be reliably correlated and are deliberately not fabricated.
Missing legacy rule fields use the documented 7/14/21/28/35/42 compatibility
baseline; the original purchase-time global configuration cannot be recovered.
Existing task dates and explicitly stored rule fields are preserved.
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a2c9e4b6d8"
down_revision = "e6f1b4c8d2a7"
branch_labels = None
depends_on = None

OLD_OUTCOMES = "outcome IN ('no_answer', 'busy', 'wrong_number', 'refused', 'needs_human', 'completed', 'blocked')"
NEW_OUTCOMES = "outcome IN ('accepted', 'no_answer', 'busy', 'wrong_number', 'refused', 'needs_human', 'completed', 'blocked')"


def _freeze(config, days):
    value = dict(config or {})
    value.setdefault("template_version", "steward_v1")
    if value.get("followup_days") is None:
        value["followup_days"] = [d for d in (7, 14, 21, 28, 35, 42) if d <= days]
    return value


def upgrade():
    with op.batch_alter_table("phone_call_attempts") as batch:
        batch.add_column(sa.Column("attempt_no", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("external_request_id", sa.String(128), nullable=True))
        batch.create_unique_constraint("uq_phone_attempts_number", ["order_id", "attempt_no"])
        batch.drop_constraint("ck_phone_attempts_outcome", type_="check")
        batch.create_check_constraint("ck_phone_attempts_outcome", NEW_OUTCOMES)

    conn = op.get_bind()
    versions = sa.table("mp_package_versions", sa.column("id", sa.Integer),
                        sa.column("fulfillment_type", sa.String), sa.column("service_days", sa.Integer),
                        sa.column("fulfillment_config", sa.JSON))
    instances = sa.table("steward_service_instances", sa.column("id", sa.Integer),
                         sa.column("package_version_id", sa.Integer), sa.column("service_days", sa.Integer),
                         sa.column("fulfillment_config", sa.JSON))
    frozen = {}
    for row in conn.execute(sa.select(versions).where(versions.c.fulfillment_type == "steward")).mappings():
        config = _freeze(row["fulfillment_config"], row["service_days"])
        frozen[row["id"]] = config
        conn.execute(versions.update().where(versions.c.id == row["id"]).values(fulfillment_config=config))
    for row in conn.execute(sa.select(instances)).mappings():
        config = {**frozen.get(row["package_version_id"], {}), **(row["fulfillment_config"] or {})}
        conn.execute(instances.update().where(instances.c.id == row["id"]).values(
            fulfillment_config=_freeze(config, row["service_days"])))


def downgrade():
    # An in-flight accepted attempt has no equivalent old outcome; retain it as blocked history.
    op.execute("UPDATE phone_call_attempts SET outcome = 'blocked' WHERE outcome = 'accepted'")
    with op.batch_alter_table("phone_call_attempts") as batch:
        batch.drop_constraint("ck_phone_attempts_outcome", type_="check")
        batch.create_check_constraint("ck_phone_attempts_outcome", OLD_OUTCOMES)
        batch.drop_constraint("uq_phone_attempts_number", type_="unique")
        batch.drop_column("external_request_id")
        batch.drop_column("attempt_no")
    # Preserve the frozen JSON as business evidence when rolling back code.
