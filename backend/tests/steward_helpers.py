"""服务履约域（工作二）测试共用夹具与工具。

走真实接口链路造数：后台建包 / 单位批次 → 用户按手机号认领权益 → 懒创建服务实例。
批次一起（S1/S2/A1）：授权条款经后台发布版本、签发计划与试点放行均为真实接口造数；
不再使用 STEWARD_*_CONSENT_TEXT 配置注入（已废止）。
"""

import uuid
from datetime import datetime, timezone

import httpx
import pytest_asyncio

from tests.commerce_helpers import API, admin_headers, make_package, user_headers

USER1 = {"id": 1, "phone": "13800000001"}

TERMS_V1 = {
    "steward_service": ("平台服务授权条款（测试 v1）", "本人同意平台在 45 天服务期内为履约目的处理本人的服务记录与必要健康信息。"),
    "steward_device_data": ("设备厂商数据授权条款（测试 v1）", "本人同意平台从设备厂商云端拉取本人设备数据用于本次服务。"),
}

RELEASE_EVIDENCE = [
    {"item": item, "source": f"测试来源-{idx}", "reference": f"TEST-REF-{idx:02d}"}
    for idx, item in enumerate(
        (
            "contract_attachment",
            "device_supply",
            "org_contract",
            "physician_staffing",
            "care_plan_rules",
            "refund_interruption",
            "duty_ops",
            "outreach_channel",
        ),
        start=1,
    )
]


async def publish_default_terms(client: httpx.AsyncClient, a: dict) -> None:
    """发布两份 v1 条款（S1：先发布后授权，版本一致才可确认）。"""
    for purpose, (title, text) in TERMS_V1.items():
        resp = await client.post(
            f"{API}/admin/steward/consent-terms",
            json={"purpose": purpose, "version": "v1", "title": title, "text": text},
            headers=a,
        )
        if resp.status_code == 409:
            continue  # 同一测试内二次造数：v1 已发布，幂等跳过
        assert resp.status_code == 201, resp.text


async def bind_test_wx_identity(client: httpx.AsyncClient, user_id: int = USER1["id"]) -> None:
    """为测试用户绑定微信身份（identity_phone 门禁要求手机号来自微信核验；
    wx 登录域的种子用户保持无 openid，互不影响其链接/冲突用例前提）。"""
    from app.models import User as UserModel

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        user = await session.get(UserModel, user_id)
        if user is not None and not user.wx_openid:
            user.wx_openid = "steward-test-openid"
            await session.commit()


async def issue_batch_entitlement(
    client: httpx.AsyncClient, a_headers: dict, pkg: dict, *, phone: str = USER1["phone"]
) -> int:
    """单位批次发放一份 pending 权益，返回权益 id（C 端列表按手机号可见）。"""
    await bind_test_wx_identity(client)
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


async def create_pilot_release(client: httpx.AsyncClient, a: dict, pkg: dict) -> dict:
    """发布覆盖当前测试环境（APP_ENV=test）的试点放行记录。"""
    from app.core.config import settings

    resp = await client.post(
        f"{API}/admin/steward/pilot-releases",
        json={
            "package_id": pkg["package_id"],
            "package_version_id": pkg["version_id"],
            "env": settings.APP_ENV,
            "owner_name": "测试放行负责人",
            "evidence": RELEASE_EVIDENCE,
        },
        headers=a,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def get_my_instance(client: httpx.AsyncClient) -> dict:
    """懒创建并取本人（用户 1）最新服务实例。"""
    resp = await client.get(f"{API}/mp/steward/instances", headers=user_headers(USER1["id"]))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert items, "应已懒创建服务实例"
    return items[-1]  # 最新实例（同一测试内多次造数时取新的一份）


async def full_setup(
    client: httpx.AsyncClient,
    *,
    phone: str = USER1["phone"],
    with_release: bool = True,
    with_device: bool = True,
) -> dict:
    """从零走到「门禁全过、可激活」状态，返回上下文。

    步骤对应新门禁：条款发布（S1）→ 三岗指派（管家≠替补）→ 医师签发计划（A1）
    → 试点放行（S2）→ 双授权（带版本确认）→ 收货人工凭据 → 设备绑定与有效数据
    （切片 C 起自动判定，经合成适配器注入）。
    """
    a = await admin_headers(client, "admin")
    pkg = await make_package(client, a, name="全域健康管家 · 45 天睡眠健康包")
    ent_id = await issue_batch_entitlement(client, a, pkg, phone=phone)
    await claim_entitlement(client, ent_id)
    instance = await get_my_instance(client)

    await publish_default_terms(client, a)

    # 先查已有有效 v1 签发计划：复用时实例须指派同一医师（门禁按医师一致判定）
    resp = await client.get(
        f"{API}/admin/steward/care-plans", params={"user_id": USER1["id"]}, headers=a
    )
    existing = [p for p in resp.json() if p["version"] == "v1" and not p["revoked_at"]]

    butler = await create_staff(client, a, "butler", "管家甲")
    backup = await create_staff(client, a, "butler", "管家乙")
    if existing:
        physician = {"id": existing[0]["physician_id"]}
        care_plan = existing[0]
    else:
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

    if not existing:
        resp = await client.post(
            f"{API}/admin/steward/care-plans",
            json={
                "user_id": USER1["id"],
                "physician_id": physician["id"],
                "version": "v1",
                "org_name": "杏临汇签约服务机构",
                "content": {"sleep_window": "23:00-07:00", "followup_focus": "睡眠效率"},
            },
            headers=a,
        )
        assert resp.status_code == 201, resp.text
        care_plan = resp.json()

    release = None
    if with_release:
        release = await create_pilot_release(client, a, pkg)

    u = user_headers(USER1["id"])
    for purpose in ("steward_service", "steward_device_data"):
        resp = await client.post(
            f"{API}/mp/steward/consents", json={"purpose": purpose, "version": "v1"}, headers=u
        )
        assert resp.status_code == 200, resp.text

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance['id']}/prep-checks/delivery/pass",
        json={"evidence": "测试凭据：收货完成（快递单已签收）"},
        headers=a,
    )
    assert resp.status_code == 200, resp.text

    if with_device:
        from tests.device_helpers import ensure_device_facts

        await ensure_device_facts(client, a)

    resp = await client.get(f"{API}/mp/steward/instances/{instance['id']}", headers=u)
    missing = [c["check_type"] for c in resp.json()["prep_checks"] if c["status"] != "passed"]
    if with_device:
        assert missing == [], f"门禁应全部通过，仍缺 {missing}"
    return {
        "admin": a,
        "user": u,
        "package": pkg,
        "entitlement_id": ent_id,
        "instance": resp.json(),
        "staff": {"butler": butler, "backup": backup, "physician": physician},
        "care_plan": care_plan,
        "release": release,
    }

@pytest_asyncio.fixture
async def file_db_steward(tmp_path):
    """文件库 + NullPool：真连接级隔离，专测激活并发（沿用工作一并发测试模式）。

    批次一起：同步补齐条款 / 签发计划 / 试点放行与微信身份种子（S1/S2/A1 门禁事实）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings
    from app.db.base import Base
    from app.models import (
        PREP_CHECK_META,
        AdminUser,
        DeviceBinding,
        DeviceMetric,
        DeviceVendor,
        EntitlementSource,
        MpEntitlement,
        MpOrder,
        MpPackage,
        MpPackageVersion,
        MpUserConsent,
        PrepCheckSource,
        PrepCheckStatus,
        StewardCarePlan,
        StewardConsentTerms,
        StewardPilotRelease,
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
        session.add(User(nickname="并发用户", phone="13800000001",
                         wx_openid="conc-wx-openid", status="active"))
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
                    instance_id=instance.id, check_type=check_type, source=source.value,
                    status=(
                        PrepCheckStatus.PASSED.value
                        if source == PrepCheckSource.MANUAL
                        else PrepCheckStatus.PENDING.value
                    ),
                )
            )
        synth_vendor = DeviceVendor(code=f"synth{uuid.uuid4().hex[:4]}", name="并发合成厂商")
        session.add(synth_vendor)
        await session.flush()
        device_binding = DeviceBinding(
            user_id=1, vendor_id=synth_vendor.id, device_model="WRIST-CONC", device_sn="SN-CONC-1"
        )
        session.add(device_binding)
        await session.flush()
        from datetime import date as date_cls

        session.add(
            DeviceMetric(
                binding_id=device_binding.id, user_id=1, metric_type="sleep_duration",
                value=400, unit="min", business_date=date_cls.today(), dedup_key="conc-1:sleep_duration",
                vendor_record_id="conc-1", vendor_updated_at=datetime.now(timezone.utc),
                quality="valid",
            )
        )
        session.add_all([
            MpUserConsent(user_id=1, purpose="steward_service", version="v1", text="并发条款"),
            MpUserConsent(user_id=1, purpose="steward_device_data", version="v1", text="并发条款"),
            StewardConsentTerms(purpose="steward_service", version="v1",
                                title="平台服务条款", text="并发条款", status="active"),
            StewardConsentTerms(purpose="steward_device_data", version="v1",
                                title="厂商数据条款", text="并发条款", status="active"),
            StewardCarePlan(user_id=1, physician_id=physician.id,
                            org_name="并发机构", version="v1",
                            content={"plan": "concurrent"}),
            StewardPilotRelease(package_id=package.id, package_version_id=version.id,
                                env=settings.APP_ENV, owner_name="并发放行人",
                                evidence=[{"item": "contract_attachment", "source": "s", "reference": "r"}]),
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
