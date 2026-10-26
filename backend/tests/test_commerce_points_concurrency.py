"""积分兑换并发：同一 client_order_id 并发下单恰好一次扣分+发权益；并发调整不透支。

沿用 test_appointment_concurrency 的文件库 + NullPool 模式：内存 StaticPool
共享单连接无法模拟连接级隔离。
"""

import asyncio
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.main as app_main
from app.core.deps import get_db, get_redis
from app.core.security import SCOPE_ADMIN, SCOPE_USER, create_token
from app.db.base import Base
from app.models import (
    MpEntitlement,
    MpOrder,
    MpPackage,
    MpPackageVersion,
    MpPointsEntry,
    User,
)
from tests.conftest import FakeRedis

API = "/api/v1"


@pytest_asyncio.fixture
async def file_db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'commerce_concurrency.db'}"
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        from app.core.security import hash_password
        from app.models import AdminUser

        admin = AdminUser(
            username="admin", name="并发管理员",
            password_hash=hash_password("Concurrent-Admin-2026!"), role="admin",
        )
        session.add(admin)
        session.add(User(nickname="并发用户", phone="13800000001", status="active"))
        package = MpPackage(name="并发服务包", subtitle=None, sort=0, status="on_shelf")
        session.add(package)
        await session.flush()
        version = MpPackageVersion(
            package_id=package.id,
            version_no=1,
            price_cents=9900,
            points_cost=300,
            validity_days=45,
            benefits=[{"name": "健康管家"}],
            service_desc="并发测试说明",
            terms="并发测试条款",
        )
        session.add(version)
        await session.flush()
        package.current_version_id = version.id
        from app.models import MpPointsAccount, MpSaleRelease

        session.add(
            MpSaleRelease(
                package_id=package.id, version_id=version.id, env="all",
                basis="并发测试放行", released_by_admin_id=admin.id,
            )
        )
        session.add(MpPointsAccount(user_id=1, balance=300))
        await session.commit()

    async def override_get_db():
        async with factory() as session:
            yield session

    app_main.app.dependency_overrides[get_db] = override_get_db
    app_main.app.dependency_overrides[get_redis] = lambda: FakeRedis()
    yield {"factory": factory, "package_id": package.id, "version_id": version.id, "admin_id": admin.id}
    app_main.app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_client_order_single_redeem(file_db):
    """6 路并发同 client_order_id 的积分兑换：恰一单成功，扣分一次，权益一份。"""
    headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}
    key = str(uuid.uuid4())
    payload = {
        "client_order_id": key,
        "package_id": file_db["package_id"],
        "version_id": file_db["version_id"],
        "pay_type": "points",
        "agree_terms": True,
    }

    async def submit(i):
        transport = httpx.ASGITransport(app=app_main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=headers
        ) as c:
            return await c.post(f"{API}/mp/orders", json=payload)

    results = await asyncio.gather(*(submit(i) for i in range(6)))
    codes = sorted(r.status_code for r in results)
    created = [r for r in results if r.status_code == 201]
    idempotent = [r for r in results if r.status_code == 200]
    assert len(created) == 1, f"应恰一单创建成功，实际 {[r.status_code for r in results]}"
    assert all(r.status_code in (200, 201, 409) for r in results)
    order_id = created[0].json()["id"]

    factory = file_db["factory"]
    async with factory() as session:
        orders = (
            await session.execute(
                select(MpOrder).where(MpOrder.client_order_id == key)
            )
        ).scalars().all()
        assert len(orders) == 1
        assert orders[0].status == "paid"
        ents = (
            await session.execute(
                select(MpEntitlement).where(MpEntitlement.order_id == orders[0].id)
            )
        ).scalars().all()
        assert len(ents) == 1
        redeems = (
            await session.execute(
                select(func.count()).select_from(MpPointsEntry).where(
                    MpPointsEntry.biz_type == "redeem"
                )
            )
        ).scalar()
        assert int(redeems) == 1
        from app.models import MpPointsAccount

        account = await session.get(MpPointsAccount, 1)
        assert account.balance == 0


@pytest.mark.asyncio
async def test_concurrent_adjust_no_overdraft(file_db):
    """6 路并发 +100 / 6 路并发 -80（余额 300）：最终余额 = 300 + 600 - 最多扣减量，永不为负。"""
    admin_headers = {
        "Authorization": f"Bearer {create_token(file_db['admin_id'], SCOPE_ADMIN, 'access')}"
    }

    async def adjust(delta, i):
        transport = httpx.ASGITransport(app=app_main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=admin_headers
        ) as c:
            return await c.post(
                f"{API}/admin/mp/points/adjust",
                json={"user_id": 1, "delta": delta, "note": f"并发{delta}-{i}"},
            )

    results = await asyncio.gather(
        *(adjust(100, i) for i in range(6)), *(adjust(-80, i) for i in range(6))
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 12, codes  # 余额充足，全部应成功

    factory = file_db["factory"]
    from app.models import MpPointsAccount

    async with factory() as session:
        account = await session.get(MpPointsAccount, 1)
        assert account.balance == 300 + 600 - 480
        # 流水条数与余额一致
        entries = (
            await session.execute(select(func.count()).select_from(MpPointsEntry))
        ).scalar()
        assert int(entries) == 12
