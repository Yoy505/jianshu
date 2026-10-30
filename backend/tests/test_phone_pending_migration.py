"""待关联事件真实迁移：状态长度、历史记录保留与降级拒绝事实。"""

import asyncio

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import PhoneCallbackEvent, PhoneCallOrder, PhoneScript
from tests.test_migration_paths import alembic
from tests.test_snapshot_migration import seed_legacy_rules

PARENT = "f7a2c9e4b6d8"


async def seed_callback_history(url):
    await seed_legacy_rules(url)
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            script = PhoneScript(code="migration", version="v1", title="话术", content="内容", target_type="self")
            db.add(script)
            await db.flush()
            order = PhoneCallOrder(instance_id=1, user_id=1, purpose="followup_reminder", target_type="self",
                                   script_id=script.id, script_code=script.code, script_version="v1", status="in_progress", attempts=1)
            db.add(order)
            await db.flush()
            db.add(PhoneCallbackEvent(order_id=order.id, event_key="history", outcome="completed",
                                      payload_sha256="a" * 64, result="stale"))
            await db.commit()
    finally:
        await engine.dispose()


async def add_pending_and_assert(url):
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as db:
            db.add(PhoneCallbackEvent(order_id=1, event_key="pending", outcome="completed",
                                      payload_sha256="b" * 64, result="pending_correlation"))
            await db.commit()
        async with engine.connect() as conn:
            cols = await conn.run_sync(lambda c: inspect(c).get_columns("phone_callback_events"))
            assert next(c["type"].length for c in cols if c["name"] == "result") == 24
    finally:
        await engine.dispose()


async def assert_downgraded_history(url):
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as db:
            rows = (await db.execute(select(PhoneCallbackEvent.result).order_by(PhoneCallbackEvent.id))).scalars().all()
            assert rows == ["stale", "rejected_state"]
        async with engine.connect() as conn:
            cols = await conn.run_sync(lambda c: inspect(c).get_columns("phone_callback_events"))
            assert next(c["type"].length for c in cols if c["name"] == "result") == 16
    finally:
        await engine.dispose()


def test_pending_event_migration_and_rollback(tmp_path):
    path = tmp_path / "pending.db"
    url = f"sqlite+aiosqlite:///{path}"
    alembic(path, "upgrade", PARENT)
    asyncio.run(seed_callback_history(url))
    alembic(path, "upgrade", "head")
    asyncio.run(add_pending_and_assert(url))
    alembic(path, "downgrade", PARENT)
    asyncio.run(assert_downgraded_history(url))
    alembic(path, "upgrade", "head")
