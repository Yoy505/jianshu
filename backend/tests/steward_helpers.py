"""服务履约域（工作二）测试共用夹具与工具。

走真实接口链路造数：后台建包 / 单位批次 → 用户按手机号认领权益 → 懒创建服务实例。
"""

import uuid

import httpx
import pytest
import pytest_asyncio

from app.core.config import settings
from tests.commerce_helpers import API, admin_headers, make_package, user_headers

USER1 = {"id": 1, "phone": "13800000001"}


@pytest.fixture
def steward_env():
    """配置两份授权条款文本；未配置时授权接口 503、门禁恒 pending（另有用例覆盖）。"""
    saved = (
        settings.STEWARD_SERVICE_CONSENT_TEXT,
        settings.STEWARD_DEVICE_CONSENT_TEXT,
    )
    settings.STEWARD_SERVICE_CONSENT_TEXT = (
        "平台服务授权条款（测试文本 v1）：本人同意平台在 45 天服务期内为履约目的处理本人的服务记录与必要健康信息。"
    )
    settings.STEWARD_DEVICE_CONSENT_TEXT = (
        "设备厂商数据授权条款（测试文本 v1）：本人同意平台从设备厂商云端拉取本人设备数据用于本次服务。"
    )
    try:
        yield
    finally:
        settings.STEWARD_SERVICE_CONSENT_TEXT, settings.STEWARD_DEVICE_CONSENT_TEXT = saved


async def issue_batch_entitlement(
    client: httpx.AsyncClient, a_headers: dict, pkg: dict, *, phone: str = USER1["phone"]
) -> int:
    """单位批次发放一份 pending 权益，返回权益 id（C 端列表按手机号可见）。"""
    suffix = uuid.uuid4().hex[:6]
    resp = await client.post(
        f"{API}/admin/mp/orgs",
        json={"name": f"测试单位{suffix}", "contact_name": "联系人", "contact_phone": "13900000000"},
        headers=a_headers,
    )
    assert resp.status_code == 201, resp.text
    org_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/batches/preview",
        json={
            "org_id": org_id,
            "package_id": pkg["package_id"],
            "version_id": pkg["version_id"],
            "quantity": 1,
            "file_name": "名单.xlsx",
            "rows": [phone],
        },
        headers=a_headers,
    )
    assert resp.status_code == 201, resp.text
    batch_id = resp.json()["batch"]["id"]
    resp = await client.post(
        f"{API}/admin/mp/batches/{batch_id}/confirm", headers=a_headers
    )
    assert resp.status_code == 200, resp.text
    resp = await client.get(
        f"{API}/mp/entitlements", params={"status": "pending"}, headers=user_headers(USER1["id"])
    )
    items = resp.json()["items"]
    assert items, "应有可认领的 pending 权益"
    return items[0]["id"]


async def claim_entitlement(client: httpx.AsyncClient, ent_id: int) -> dict:
    resp = await client.post(
        f"{API}/mp/entitlements/{ent_id}/claim", headers=user_headers(USER1["id"])
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def create_staff(client: httpx.AsyncClient, a_headers: dict, role: str, name: str) -> dict:
    resp = await client.post(
        f"{API}/admin/steward/staff",
        json={
            "name": name,
            "phone": "13700000001",
            "role": role,
            "org_name": "杏临汇签约服务机构",
            "title": "健康管家" if role == "butler" else "主治医师",
            **({"license_no": "110101199001011234"} if role == "physician" else {}),
        },
        headers=a_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get_my_instance(client: httpx.AsyncClient) -> dict:
    """懒创建并取本人（用户 1）最新服务实例。"""
    resp = await client.get(f"{API}/mp/steward/instances", headers=user_headers(USER1["id"]))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert items, "应已懒创建服务实例"
    return items[0]


async def full_setup(client: httpx.AsyncClient, *, phone: str = USER1["phone"]) -> dict:
    """从零走到「门禁全过、可激活」状态，返回上下文。"""
    a = await admin_headers(client, "admin")
    pkg = await make_package(client, a, name="全域健康管家 · 45 天睡眠健康包")
    ent_id = await issue_batch_entitlement(client, a, pkg, phone=phone)
    await claim_entitlement(client, ent_id)
    instance = await get_my_instance(client)

    butler = await create_staff(client, a, "butler", "管家甲")
    backup = await create_staff(client, a, "butler", "管家乙")
    physician = await create_staff(client, a, "physician", "医师丙")
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance['id']}/assign",
        json={
            "butler_id": butler["id"],
            "backup_butler_id": backup["id"],
            "physician_id": physician["id"],
        },
        headers=a,
    )
    assert resp.status_code == 200, resp.text

    u = user_headers(USER1["id"])
    for purpose in ("steward_service", "steward_device_data"):
        resp = await client.post(
            f"{API}/mp/steward/consents", json={"purpose": purpose}, headers=u
        )
        assert resp.status_code == 200, resp.text

    for check_type in ("delivery", "device_paired", "first_valid_data", "physician_plan"):
        resp = await client.post(
            f"{API}/admin/steward/instances/{instance['id']}/prep-checks/{check_type}/pass",
            json={"evidence": f"测试凭据：{check_type} 已完成（快递单/配对记录/数据校验/初评编号）"},
            headers=a,
        )
        assert resp.status_code == 200, resp.text

    resp = await client.get(f"{API}/mp/steward/instances/{instance['id']}", headers=u)
    missing = [c["check_type"] for c in resp.json()["prep_checks"] if c["status"] != "passed"]
    assert missing == [], f"门禁应全部通过，仍缺 {missing}"
    return {
        "admin": a,
        "user": u,
        "package": pkg,
        "entitlement_id": ent_id,
        "instance": resp.json(),
        "staff": {"butler": butler, "backup": backup, "physician": physician},
    }


@pytest_asyncio.fixture
async def file_db_steward(tmp_path):
    """文件库 + NullPool：真连接级隔离，专测激活并发（沿用工作一并发测试模式）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db.base import Base
    from app.models import (
        PREP_CHECK_META,
        AdminUser,
        EntitlementSource,
        MpEntitlement,
        MpOrder,
        MpPackage,
        MpPackageVersion,
        MpUserConsent,
        PrepCheckSource,
        PrepCheckStatus,
        StewardPrepCheck,
        StewardServiceInstance,
        StewardStaff,
        User,
    )
    from app.core.security import hash_password

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'steward_concurrency.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with factory() as session:
        session.add(AdminUser(username="admin", name="并发管理员",
                              password_hash=hash_password("Concurrent-Admin-2026!"), role="admin"))
        session.add(User(nickname="并发用户", phone="13800000001", status="active"))
        package = MpPackage(name="并发服务包", status="on_shelf")
        session.add(package)
        await session.flush()
        version = MpPackageVersion(
            package_id=package.id, version_no=1, price_cents=9900, validity_days=45,
            benefits=[{"name": "健康管家", "desc": "45 天并发"}],
            service_desc="并发说明", terms="并发条款",
        )
        session.add(version)
        await session.flush()
        package.current_version_id = version.id
        order = MpOrder(
            order_no="SPCONC0001", user_id=1, client_order_id="conc-1",
            package_id=package.id, package_version_id=version.id,
            package_name="并发服务包", version_no=1, pay_type="points",
            total_cents=9900, points_cost=100, status="paid", terms_version="v1",
        )
        session.add(order)
        await session.flush()
        entitlement = MpEntitlement(
            source_type=EntitlementSource.ORDER.value, order_id=order.id, user_id=1,
            package_id=package.id, package_version_id=version.id,
            package_name="并发服务包", version_no=1, benefits=version.benefits,
            validity_days=45, status="active",
        )
        session.add(entitlement)
        await session.flush()
        butler = StewardStaff(name="管家甲", role="butler", status="active")
        backup = StewardStaff(name="管家乙", role="butler", status="active")
        physician = StewardStaff(name="医师丙", role="physician", status="active")
        session.add_all([butler, backup, physician])
        await session.flush()
        instance = StewardServiceInstance(
            instance_no="SLCONC0001", entitlement_id=entitlement.id, user_id=1,
            package_id=package.id, package_version_id=version.id,
            package_name="并发服务包", version_no=1, benefits=version.benefits,
            validity_days=45, service_days=45,
            butler_id=butler.id, backup_butler_id=backup.id, physician_id=physician.id,
        )
        session.add(instance)
        await session.flush()
        for check_type, (_title, source) in PREP_CHECK_META.items():
            session.add(
                StewardPrepCheck(
                    instance_id=instance.id,
                    check_type=check_type,
                    source=source.value,
                    status=(
                        PrepCheckStatus.PASSED.value
                        if source == PrepCheckSource.MANUAL
                        else PrepCheckStatus.PENDING.value
                    ),
                )
            )
        session.add_all([
            MpUserConsent(user_id=1, purpose="steward_service", version="v1", text="并发条款"),
            MpUserConsent(user_id=1, purpose="steward_device_data", version="v1", text="并发条款"),
        ])
        await session.commit()

    async def override_get_db():
        async with factory() as session:
            yield session

    yield {
        "factory": factory,
        "override_get_db": override_get_db,
        "instance_id": instance.id,
        "entitlement_id": entitlement.id,
    }
    await engine.dispose()
