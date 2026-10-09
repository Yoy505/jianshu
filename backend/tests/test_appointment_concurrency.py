"""预约并发防重：数据库约束保证同用户同日同时段唯一（审核报告 P2 订正）。

并发用例使用文件型 SQLite + NullPool（每会话独立连接）：
conftest 的内存 StaticPool 共享单连接，并发事务会互相回滚，
无法真实模拟 PostgreSQL 的连接级隔离（调试已证：成功提交被并发
409 的 rollback 卷走）。serial/rebook 用例沿用 client 夹具。

验证点：
- 并发提交同槽位 → 恰好一个 201、其余 409，库内仅一条进行中记录；
- 串行重复 → 409（应用层预检）；
- cancelled 后同槽位可再约（约束只作用于 pending/confirmed）；
- 不同时段互不影响。
"""

import asyncio
from datetime import date, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.main as app_main
from app.core.deps import get_db, get_redis
from app.core.security import SCOPE_USER, create_token
from app.db.base import Base
from app.models import MpAppointment, MpProvider, MpProviderService, MpService, User
from tests.conftest import FakeRedis

DAY_OFFSETS = {
    name: (date.today() + timedelta(days=off)).isoformat()
    for name, off in (
        ("concurrent", 5),
        ("serial", 2),
        ("rebook", 3),
        ("periods", 4),
    )
}


def _payload(day, period="上午", service_id=8, provider_id=3, **extra):
    base = {
        "service_id": service_id,
        "provider_id": provider_id,
        "date": day,
        "period": period,
        "contact_name": "并发测试",
        "contact_phone": "13800000001",
        "address": "天津市滨海新区",
    }
    base.update(extra)
    return base


@pytest_asyncio.fixture
async def file_db(tmp_path):
    """文件库 + NullPool：并发会话真实连接隔离，等价模拟 PG 行为。"""
    url = f"sqlite+aiosqlite:///{tmp_path / 'concurrency.db'}"
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 最小种子：user 1 + 服务/人员挂接（与 payload 对齐）
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        session.add(User(nickname="并发用户", phone="13800000001", status="active"))
        provider = MpProvider(
            name="并发测试人员", gender="male", title="康复治疗师", dept="康复医学科",
            org="并发测试医院", years=5, skills=[], intro="t", rating=4.9,
            service_count=0, online=True,
        )
        session.add(provider)
        await session.flush()
        service = MpService(
            scenario_key="rehab",
            service_type="offline",
            name="并发测试服务",
            desc="d",
            price=100,
            unit="次",
            duration_min=30,
            sales=0,
            on_shelf=True,
        )
        session.add(service)
        await session.flush()
        session.add(MpProviderService(service_id=service.id, provider_id=provider.id))
        await session.commit()

    async def override_get_db():
        async with factory() as session:
            yield session

    redis_stub = FakeRedis()
    app_main.app.dependency_overrides[get_db] = override_get_db
    app_main.app.dependency_overrides[get_redis] = lambda: redis_stub
    yield factory
    app_main.app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_slot_only_one_succeeds(file_db):
    """同一用户并发提交同日同时段 6 个预约：恰好 1 个 201，其余 409。"""
    headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}
    day = DAY_OFFSETS["concurrent"]

    # 取种子生成的 service id（payload 引用）
    async with file_db() as session:
        svc = (
            await session.execute(
                select(MpService).where(MpService.name == "并发测试服务")
            )
        ).scalar_one()
    async with file_db() as session:
        prov = (
            await session.execute(select(MpProvider).where(MpProvider.name == "并发测试人员"))
        ).scalar_one()
    payload_tmpl = _payload(day, service_id=svc.id, provider_id=prov.id)

    async def submit(i):
        transport = httpx.ASGITransport(app=app_main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=headers
        ) as c:
            return await c.post(
                "/api/v1/mp/appointments",
                json=dict(payload_tmpl, contact_name=f"并发{i}"),
            )

    results = await asyncio.gather(*(submit(i) for i in range(6)))
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1, f"应恰好一个成功，实际 {codes}"
    assert codes.count(409) == 5, f"其余应 409，实际 {codes}"
    for r in results:
        if r.status_code == 409:
            assert r.json()["detail"] == "该时段已有进行中的预约"

    # 库内核验（直查数据库，不经列表接口）
    async with file_db() as session:
        active = (
            await session.execute(
                select(func.count()).select_from(MpAppointment).where(
                    MpAppointment.user_id == 1,
                    MpAppointment.appt_date == date.fromisoformat(day),
                    MpAppointment.period == "上午",
                    MpAppointment.status.in_(["pending", "confirmed"]),
                )
            )
        ).scalar()
    assert int(active or 0) == 1


@pytest.mark.asyncio
async def test_serial_duplicate_still_409(client):
    """串行路径回归：先 201 后 409（应用层预检可读错误）。"""
    headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}
    day = DAY_OFFSETS["serial"]
    first = await client.post(
        "/api/v1/mp/appointments", json=_payload(day, period="下午"), headers=headers
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/mp/appointments",
        json=_payload(day, period="下午", service_id=1, provider_id=1),
        headers=headers,
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_cancelled_slot_can_be_rebooked(client):
    """取消后同槽位可再约：约束仅覆盖 pending/confirmed。"""
    headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}
    day = DAY_OFFSETS["rebook"]
    first = await client.post(
        "/api/v1/mp/appointments", json=_payload(day, period="晚间"), headers=headers
    )
    assert first.status_code == 201
    appt_id = first.json()["id"]

    cancel = await client.post(
        f"/api/v1/mp/appointments/{appt_id}/cancel", headers=headers
    )
    assert cancel.status_code == 200

    rebook = await client.post(
        "/api/v1/mp/appointments", json=_payload(day, period="晚间"), headers=headers
    )
    assert rebook.status_code == 201


@pytest.mark.asyncio
async def test_different_periods_not_blocked(client):
    """不同时段互不影响（约束键为 user+date+period）。"""
    headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}
    day = DAY_OFFSETS["periods"]
    a = await client.post(
        "/api/v1/mp/appointments", json=_payload(day, period="上午"), headers=headers
    )
    b = await client.post(
        "/api/v1/mp/appointments", json=_payload(day, period="下午"), headers=headers
    )
    assert a.status_code == 201 and b.status_code == 201
