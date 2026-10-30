"""Exercise the data migration on missing, explicit empty and customized snapshots."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import MpEntitlement, MpOrder, MpPackage, MpPackageVersion, StewardServiceInstance, User
from tests.test_migration_paths import alembic

PREVIOUS_HEAD = "e6f1b4c8d2a7"
CONFIGS = [None, {"followup_days": []}, {"template_version": "custom", "followup_days": [5, 10]}]
EXPECTED = [
    {"template_version": "steward_v1", "followup_days": [7, 14, 21, 28]},
    {"template_version": "steward_v1", "followup_days": []},
    {"template_version": "custom", "followup_days": [5, 10]},
]


async def seed_legacy_rules(url):
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            user = User(nickname="迁移用户", phone="13800000001", status="active")
            package = MpPackage(name="存量商品")
            db.add_all([user, package])
            await db.flush()
            for no, config in enumerate(CONFIGS, 1):
                version = MpPackageVersion(package_id=package.id, version_no=no, price_cents=100,
                    validity_days=90, service_desc="存量说明", terms="存量条款", fulfillment_type="steward",
                    service_days=30, fulfillment_config=config)
                db.add(version)
                await db.flush()
                snapshot = {"package_id": package.id, "package_version_id": version.id,
                            "package_name": package.name, "version_no": no, "user_id": user.id}
                order = MpOrder(**snapshot, order_no=f"ORDER-{no}", client_order_id=f"CLIENT-{no}",
                                total_cents=100, pay_type="wxpay", terms_version="v1")
                db.add(order)
                await db.flush()
                entitlement = MpEntitlement(**snapshot, source_type="order", order_id=order.id,
                                            validity_days=90, status="active")
                db.add(entitlement)
                await db.flush()
                db.add(StewardServiceInstance(**snapshot, instance_no=f"INST-{no}",
                    entitlement_id=entitlement.id, benefits=[], validity_days=90, service_days=30,
                    fulfillment_config=config))
            await db.commit()
    finally:
        await engine.dispose()


async def assert_frozen_rules(url):
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as db:
            for model in (MpPackageVersion, StewardServiceInstance):
                rows = (await db.execute(select(model.fulfillment_config).order_by(model.id))).scalars().all()
                assert rows == EXPECTED
    finally:
        await engine.dispose()


def test_legacy_snapshot_backfill_and_rollback(tmp_path):
    path = tmp_path / "snapshot.db"
    url = f"sqlite+aiosqlite:///{path}"
    alembic(path, "upgrade", PREVIOUS_HEAD)
    asyncio.run(seed_legacy_rules(url))
    alembic(path, "upgrade", "head")
    asyncio.run(assert_frozen_rules(url))
    alembic(path, "downgrade", PREVIOUS_HEAD)
    asyncio.run(assert_frozen_rules(url))  # 回退保留已冻结的业务证据
    alembic(path, "upgrade", "head")
    asyncio.run(assert_frozen_rules(url))
