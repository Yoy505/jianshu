"""工作二 · 服务实例与激活门禁用例（契约 docs/api.md 第五部分）。

覆盖：懒创建幂等、九项门禁逐项缺失阻断、授权撤回回退、激活幂等与并发、
45×24h 起止精确、越权 404、无手机号身份不通过、条款未配置 503。
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import MpUserConsent, StewardServiceInstance, User
from tests.commerce_helpers import API, user_headers
from tests.steward_helpers import full_setup, get_my_instance

pytestmark = pytest.mark.asyncio


async def test_lazy_instance_created_once(client, steward_env):
    """权益认领后懒创建唯一 preparing 实例；重复读取不多开。"""
    ctx = await full_setup(client)
    instance = ctx["instance"]
    assert instance["status"] == "preparing"
    assert instance["activated_at"] is None
    assert instance["package_name"] == "全域健康管家 · 45 天睡眠健康包"
    assert instance["service_days"] == 45

    again = await get_my_instance(client)
    assert again["id"] == instance["id"]
    resp = await client.get(f"{API}/mp/steward/instances", headers=user_headers(1))
    assert len(resp.json()) == 1


async def test_gate_lists_all_missing_then_shrinks(client):
    """门禁逐项缺失可见；按项补齐后缺项清单收缩到空。"""
    from tests.commerce_helpers import admin_headers, make_package
    from tests.steward_helpers import (
        claim_entitlement,
        create_staff,
        issue_batch_entitlement,
    )

    a = await admin_headers(client, "admin")
    pkg = await make_package(client, a, name="全域健康管家 · 45 天睡眠健康包")
    ent_id = await issue_batch_entitlement(client, a, pkg)
    await claim_entitlement(client, ent_id)
    instance = await get_my_instance(client)

    u = user_headers(1)
    resp = await client.get(f"{API}/mp/steward/instances/{instance['id']}", headers=u)
    checks = {c["check_type"]: c for c in resp.json()["prep_checks"]}
    # 自动项：身份与权益直接通过；其余七项缺失
    assert checks["identity_phone"]["status"] == "passed"
    assert checks["entitlement_valid"]["status"] == "passed"
    for check_type in (
        "delivery",
        "device_paired",
        "device_vendor_consent",
        "platform_consent",
        "first_valid_data",
        "physician_plan",
        "staffing_ready",
    ):
        assert checks[check_type]["status"] == "pending", check_type
        assert checks[check_type]["missing_reason"], check_type

    # 激活被阻断并返回缺项清单
    resp = await client.post(f"{API}/mp/steward/instances/{instance['id']}/activate", headers=u)
    assert resp.status_code == 409, resp.text
    missing = set(resp.json()["detail"]["missing"])
    assert missing == {
        "delivery",
        "device_paired",
        "device_vendor_consent",
        "platform_consent",
        "first_valid_data",
        "physician_plan",
        "staffing_ready",
    }

    # auto 项不能人工置通过
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance['id']}/prep-checks/staffing_ready/pass",
        json={"evidence": "试图人工通过自动项"},
        headers=a,
    )
    assert resp.status_code == 409

    # 指派齐三岗 → staffing_ready 过
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
    assert resp.status_code == 200
    resp = await client.post(f"{API}/mp/steward/instances/{instance['id']}/activate", headers=u)
    assert resp.status_code == 409
    assert "staffing_ready" not in set(resp.json()["detail"]["missing"])

    # 角色不匹配的指派被拒
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance['id']}/assign",
        json={"physician_id": butler["id"]},
        headers=a,
    )
    assert resp.status_code == 422


async def test_consent_revoked_reblocks_gate(client, steward_env):
    """撤回厂商数据授权后对应门禁回退，激活再次被阻断。"""
    ctx = await full_setup(client)
    u, a = ctx["user"], ctx["admin"]
    instance_id = ctx["instance"]["id"]

    resp = await client.get(
        f"{API}/mp/consents", params={"purpose": "steward_device_data"}, headers=u
    )
    consent_id = resp.json()["items"][0]["id"]
    resp = await client.post(f"{API}/mp/consents/{consent_id}/revoke", headers=u)
    assert resp.status_code == 200

    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 409
    assert "device_vendor_consent" in resp.json()["detail"]["missing"]


async def test_consent_text_unconfigured_503(client):
    """条款文本未配置时授权接口 503，不生成授权记录。"""
    from app.core.config import settings
    from tests.commerce_helpers import admin_headers, make_package
    from tests.steward_helpers import claim_entitlement, issue_batch_entitlement

    saved = settings.STEWARD_DEVICE_CONSENT_TEXT
    settings.STEWARD_DEVICE_CONSENT_TEXT = ""
    try:
        a = await admin_headers(client, "admin")
        pkg = await make_package(client, a)
        ent_id = await issue_batch_entitlement(client, a, pkg)
        await claim_entitlement(client, ent_id)
        resp = await client.post(
            f"{API}/mp/steward/consents",
            json={"purpose": "steward_device_data"},
            headers=user_headers(1),
        )
        assert resp.status_code == 503
    finally:
        settings.STEWARD_DEVICE_CONSENT_TEXT = saved


async def test_activate_idempotent_and_exact_45d(client, steward_env):
    """激活幂等（顺序重复）：返回原实例；ends_at 精确 45×24h；不重复生成日历。"""
    ctx = await full_setup(client)
    u = ctx["user"]
    instance_id = ctx["instance"]["id"]

    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["status"] == "active"

    activated_at = datetime.fromisoformat(first["activated_at"].replace("Z", "+00:00"))
    ends_at = datetime.fromisoformat(first["ends_at"].replace("Z", "+00:00"))
    assert abs((ends_at - activated_at) - timedelta(days=45)) < timedelta(seconds=5)

    # 重复激活仍返回原实例，不重复生成日历
    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 200 and resp.json()["status"] == "active"

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await session.execute(
            select(StewardServiceInstance).where(
                StewardServiceInstance.entitlement_id == ctx["entitlement_id"]
            )
        )
        assert len(result.scalars().all()) == 1


async def test_concurrent_activate_single_instance(file_db_steward, steward_env):
    """真并发激活（文件库 + NullPool 连接级隔离）：恰一个实例、一份日历、一次激活事件。"""
    import httpx

    import app.main as app_main
    from app.core.deps import get_db, get_redis
    from app.core.security import create_token
    from app.models import StewardTask, StewardTimelineEvent
    from tests.conftest import FakeRedis

    ctx = file_db_steward
    app_main.app.dependency_overrides[get_db] = ctx["override_get_db"]
    app_main.app.dependency_overrides[get_redis] = lambda: FakeRedis()
    try:
        transport = httpx.ASGITransport(app=app_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            u = {"Authorization": f"Bearer {create_token(1, 'user', 'access')}"}
            results = await asyncio.gather(
                *[
                    c.post(f"{API}/mp/steward/instances/{ctx['instance_id']}/activate", headers=u)
                    for _ in range(5)
                ]
            )
            assert all(r.status_code == 200 for r in results), [r.text for r in results]
            assert all(r.json()["status"] == "active" for r in results)
            assert all(r.json()["id"] == ctx["instance_id"] for r in results)

        async with ctx["factory"]() as session:
            instances = (
                (
                    await session.execute(
                        select(StewardServiceInstance).where(
                            StewardServiceInstance.entitlement_id == ctx["entitlement_id"]
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(instances) == 1
            assert instances[0].status == "active"
            tasks = (await session.execute(select(StewardTask))).scalars().all()
            assert len(tasks) == 45 + 6 + 3  # 恰一份日历
            activated_events = (
                (
                    await session.execute(
                        select(StewardTimelineEvent).where(
                            StewardTimelineEvent.event_type == "activated"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(activated_events) == 1
    finally:
        app_main.app.dependency_overrides.clear()


async def test_cross_user_and_editor_permissions(client, steward_env):
    """跨用户实例一律 404；editor 可读 steward.read 接口但不可 manage。"""
    from tests.commerce_helpers import admin_headers

    ctx = await full_setup(client)
    instance_id = ctx["instance"]["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        other_user = User(nickname="用户乙", phone="13800000002", status="active")
        session.add(other_user)
        await session.commit()

    other = user_headers(other_user.id)
    for method, path in (
        ("get", f"/mp/steward/instances/{instance_id}"),
        ("post", f"/mp/steward/instances/{instance_id}/activate"),
        ("get", f"/mp/steward/instances/{instance_id}/calendar?month=2026-11"),
        ("get", f"/mp/steward/instances/{instance_id}/timeline"),
    ):
        resp = await getattr(client, method)(f"{API}{path}", headers=other)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"

    editor = await admin_headers(client, "editor")
    resp = await client.get(f"{API}/admin/steward/instances", headers=editor)
    assert resp.status_code == 200
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/prep-checks/delivery/pass",
        json={"evidence": "越权尝试"},
        headers=editor,
    )
    assert resp.status_code == 403


async def test_user_without_phone_fails_identity(client):
    """无微信手机号的受益人：identity_phone 自动项不通过。"""
    from app.core.config import settings
    from app.models import EntitlementSource, MpEntitlement, MpOrder, MpPackageVersion
    from tests.commerce_helpers import admin_headers, make_package

    saved = settings.STEWARD_SERVICE_CONSENT_TEXT
    settings.STEWARD_SERVICE_CONSENT_TEXT = "条款（测试）"
    try:
        a = await admin_headers(client, "admin")
        pkg = await make_package(client, a)
        async with client.session_factory() as session:  # type: ignore[attr-defined]
            nobody = User(nickname="无号码用户", status="active")
            session.add(nobody)
            await session.flush()
            version = await session.get(MpPackageVersion, pkg["version_id"])
            order = MpOrder(
                order_no=f"SPTEST{nobody.id:06d}",
                user_id=nobody.id,
                client_order_id=f"direct-{nobody.id}",
                package_id=pkg["package_id"],
                package_version_id=pkg["version_id"],
                package_name="全域健康管家 · 45 天睡眠健康包",
                version_no=version.version_no,
                pay_type="points",
                total_cents=version.price_cents,
                points_cost=1,
                status="paid",
                terms_version=f"v{version.version_no}",
            )
            session.add(order)
            await session.flush()
            session.add(
                MpEntitlement(
                    source_type=EntitlementSource.ORDER.value,
                    order_id=order.id,
                    user_id=nobody.id,
                    package_id=pkg["package_id"],
                    package_version_id=pkg["version_id"],
                    package_name=order.package_name,
                    version_no=version.version_no,
                    benefits=version.benefits,
                    validity_days=version.validity_days,
                    status="active",
                )
            )
            await session.commit()
            user_id = nobody.id

        resp = await client.get(f"{API}/mp/steward/instances", headers=user_headers(user_id))
        assert resp.status_code == 200
        detail = resp.json()[0]
        resp = await client.get(f"{API}/mp/steward/instances/{detail['id']}", headers=user_headers(user_id))
        checks = {c["check_type"]: c for c in resp.json()["prep_checks"]}
        assert checks["identity_phone"]["status"] == "pending"
        assert "identity_phone" in (
            await client.post(
                f"{API}/mp/steward/instances/{detail['id']}/activate",
                headers=user_headers(user_id),
            )
        ).json()["detail"]["missing"]
    finally:
        settings.STEWARD_SERVICE_CONSENT_TEXT = saved
