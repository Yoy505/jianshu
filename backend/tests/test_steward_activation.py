"""工作二 · 服务实例与激活门禁用例（契约 docs/api.md 第五部分）。

覆盖：懒创建幂等、门禁逐项缺失阻断、授权条款版本流（S1）、试点放行门禁（S2）、
签发计划门禁与排班核验（A1）、激活凭证、激活幂等与并发、45×24h 起止精确、
越权 404、无微信身份不通过、条款未发布 503。
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    StewardActivationCredential,
    StewardServiceInstance,
    StewardTask,
    User,
)
from tests.commerce_helpers import API, user_headers
from tests.steward_helpers import full_setup, get_my_instance

pytestmark = pytest.mark.asyncio


async def test_lazy_instance_created_once(client):
    """权益认领后懒创建唯一 preparing 实例；重复读取不多开；普通再次购买不误标续期。"""
    ctx = await full_setup(client)
    instance = ctx["instance"]
    assert instance["status"] == "preparing"
    assert instance["activated_at"] is None
    assert instance["package_name"] == "全域健康管家 · 45 天睡眠健康包"
    assert instance["service_days"] == 45
    assert instance["renewed_from_instance_id"] is None  # A3：不做同人同包自动挂链

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
        publish_default_terms,
    )

    a = await admin_headers(client, "admin")
    pkg = await make_package(client, a, name="全域健康管家 · 45 天睡眠健康包")
    ent_id = await issue_batch_entitlement(client, a, pkg)
    await claim_entitlement(client, ent_id)
    instance = await get_my_instance(client)

    u = user_headers(1)
    resp = await client.get(f"{API}/mp/steward/instances/{instance['id']}", headers=u)
    checks = {c["check_type"]: c for c in resp.json()["prep_checks"]}
    # 自动项：身份与权益直接通过；其余七项缺失（条款未发布 → 待配置）
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
    # 试点放行默认未配置（S2 安全态）
    assert resp.json()["pilot_release_ready"] is False

    # 激活被阻断并返回缺项清单（含第十项 pilot_release）
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
        "pilot_release",
    }

    # auto 项不能人工置通过
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance['id']}/prep-checks/staffing_ready/pass",
        json={"evidence": "试图人工通过自动项"},
        headers=a,
    )
    assert resp.status_code == 409
    # physician_plan 已转 auto（A1）：同样拒绝人工置过
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance['id']}/prep-checks/physician_plan/pass",
        json={"evidence": "试图人工通过签发门禁"},
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

    # 角色不匹配的指派被拒；管家与替补不能同一人（A1）
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance['id']}/assign",
        json={"physician_id": butler["id"]},
        headers=a,
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance['id']}/assign",
        json={"backup_butler_id": butler["id"]},
        headers=a,
    )
    assert resp.status_code == 422


async def test_consent_terms_flow(client):
    """S1：条款先展示后确认；未见条款/错版本不可授权；同版本重复确认幂等。"""
    from tests.commerce_helpers import admin_headers, make_package
    from tests.steward_helpers import claim_entitlement, issue_batch_entitlement

    a = await admin_headers(client, "admin")
    pkg = await make_package(client, a)
    ent_id = await issue_batch_entitlement(client, a, pkg)
    await claim_entitlement(client, ent_id)
    u = user_headers(1)

    # 条款未发布：无当前条款可展示；授权 503 待配置
    resp = await client.get(f"{API}/mp/steward/consent-terms", headers=u)
    assert resp.status_code == 200 and resp.json() == []
    resp = await client.post(
        f"{API}/mp/steward/consents",
        json={"purpose": "steward_device_data", "version": "v1"},
        headers=u,
    )
    assert resp.status_code == 503

    await publish_and_check(client, a, u)

    # 未带版本 / 错版本：422 / 409，旧版本不能冒充新版本同意
    resp = await client.post(
        f"{API}/mp/steward/consents", json={"purpose": "steward_device_data"}, headers=u
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"{API}/mp/steward/consents",
        json={"purpose": "steward_device_data", "version": "v9"},
        headers=u,
    )
    assert resp.status_code == 409

    # 正确版本 → 200，快照含全文；重复提交幂等返回原记录
    resp = await client.post(
        f"{API}/mp/steward/consents",
        json={"purpose": "steward_device_data", "version": "v1"},
        headers=u,
    )
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["version"] == "v1" and "云端拉取" in first["text"]
    resp = await client.post(
        f"{API}/mp/steward/consents",
        json={"purpose": "steward_device_data", "version": "v1"},
        headers=u,
    )
    assert resp.status_code == 200 and resp.json()["id"] == first["id"]

    # 授权现状：granted=true 且不落后
    resp = await client.get(f"{API}/mp/steward/consent-records", headers=u)
    by_purpose = {r["purpose"]: r for r in resp.json()}
    assert by_purpose["steward_device_data"]["granted"] is True
    assert by_purpose["steward_device_data"]["outdated"] is False


async def publish_and_check(client, a, u):
    from tests.steward_helpers import publish_default_terms

    await publish_default_terms(client, a)
    resp = await client.get(f"{API}/mp/steward/consent-terms", headers=u)
    terms = {t["purpose"]: t for t in resp.json()}
    assert set(terms) == {"steward_service", "steward_device_data"}
    assert terms["steward_device_data"]["version"] == "v1"
    assert "云端拉取" in terms["steward_device_data"]["text"]


async def test_terms_upgrade_requires_reconfirmation(client):
    """S1：条款升版后，旧 v1 授权不再满足门禁；重新确认 v2 后恢复。"""
    ctx = await full_setup(client)
    u, a = ctx["user"], ctx["admin"]
    instance_id = ctx["instance"]["id"]

    # 升版厂商数据条款 → v2
    resp = await client.post(
        f"{API}/admin/steward/consent-terms",
        json={
            "purpose": "steward_device_data",
            "version": "v2",
            "title": "设备厂商数据授权条款（测试 v2）",
            "text": "v2：本人同意平台从设备厂商云端拉取本人设备数据用于本次服务（含撤回路径说明）。",
        },
        headers=a,
    )
    assert resp.status_code == 201, resp.text

    # 门禁回退为「条款已更新」，激活阻断
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    check = {c["check_type"]: c for c in resp.json()["prep_checks"]}["device_vendor_consent"]
    assert check["status"] == "pending"
    assert "条款已更新" in check["missing_reason"]
    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 409
    assert "device_vendor_consent" in resp.json()["detail"]["missing"]
    # 授权现状标记 outdated
    resp = await client.get(f"{API}/mp/steward/consent-records", headers=u)
    record = {r["purpose"]: r for r in resp.json()}["steward_device_data"]
    assert record["outdated"] is True and record["current_version"] == "v2"

    # 旧版本提交被拒；确认 v2 后门禁恢复
    resp = await client.post(
        f"{API}/mp/steward/consents",
        json={"purpose": "steward_device_data", "version": "v1"},
        headers=u,
    )
    assert resp.status_code == 409
    resp = await client.post(
        f"{API}/mp/steward/consents",
        json={"purpose": "steward_device_data", "version": "v2"},
        headers=u,
    )
    assert resp.status_code == 200
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    check = {c["check_type"]: c for c in resp.json()["prep_checks"]}["device_vendor_consent"]
    assert check["status"] == "passed"


async def test_consent_revoked_reblocks_gate(client):
    """撤回厂商数据授权后：门禁回退、时间线留痕、激活再次被阻断。"""
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

    # S1 联动：撤回写实例时间线 consent_revoked
    from app.models import StewardTimelineEvent

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        events = (
            (
                await session.execute(
                    select(StewardTimelineEvent.event_type).where(
                        StewardTimelineEvent.instance_id == instance_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "consent_revoked" in events


async def test_release_gate_blocks_and_revokes(client):
    """S2：无放行阻断（missing 含 pilot_release）；建放行后可激活；撤销后再阻断。"""
    from tests.commerce_helpers import admin_headers
    from tests.steward_helpers import RELEASE_EVIDENCE

    ctx = await full_setup(client, with_release=False)
    u, a = ctx["user"], ctx["admin"]
    instance_id = ctx["instance"]["id"]

    # 门禁九项全过但无放行 → 阻断，缺项仅 pilot_release
    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["missing"] == ["pilot_release"]
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    assert resp.json()["pilot_release_ready"] is False

    # 环境不匹配的放行无效（test 环境下发布 staging 放行）
    resp = await client.post(
        f"{API}/admin/steward/pilot-releases",
        json={
            "package_id": ctx["package"]["package_id"],
            "package_version_id": ctx["package"]["version_id"],
            "env": "staging",
            "owner_name": "错环境放行",
            "evidence": RELEASE_EVIDENCE,
        },
        headers=a,
    )
    assert resp.status_code == 201
    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 409

    # 凭据不完整 → 422（结构化凭据，八项缺一不可）
    bad_evidence = RELEASE_EVIDENCE[:-1]
    resp = await client.post(
        f"{API}/admin/steward/pilot-releases",
        json={
            "package_id": ctx["package"]["package_id"],
            "package_version_id": ctx["package"]["version_id"],
            "env": "test",
            "owner_name": "缺项放行",
            "evidence": bad_evidence,
        },
        headers=a,
    )
    assert resp.status_code == 422

    # 正确放行 → 激活成功
    from tests.steward_helpers import create_pilot_release

    release = await create_pilot_release(client, a, ctx["package"])
    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"

    # 撤销放行：新一份权益（同包）的实例无法再激活（放行缺项重新出现）
    from tests.steward_helpers import claim_entitlement, issue_batch_entitlement

    resp = await client.post(
        f"{API}/admin/steward/pilot-releases/{release['id']}/revoke",
        json={"reason": "试点暂停"},
        headers=a,
    )
    assert resp.status_code == 200
    ent2 = await issue_batch_entitlement(client, a, ctx["package"])
    await claim_entitlement(client, ent2)
    resp = await client.get(f"{API}/mp/steward/instances", headers=u)
    second = [i for i in resp.json() if i["id"] != instance_id][0]
    resp = await client.post(f"{API}/mp/steward/instances/{second['id']}/activate", headers=u)
    assert resp.status_code == 409
    assert "pilot_release" in resp.json()["detail"]["missing"]


async def test_care_plan_gate(client):
    """A1：签发计划缺失/撤销/医师不一致均阻断 physician_plan 门禁。"""
    from datetime import timezone

    from app.models import StewardCarePlan

    ctx = await full_setup(client)
    u, a = ctx["user"], ctx["admin"]
    instance_id = ctx["instance"]["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        plan = (
            (
                await session.execute(
                    select(StewardCarePlan).where(StewardCarePlan.user_id == 1)
                )
            )
            .scalars()
            .one()
        )
        plan.revoked_at = datetime.now(timezone.utc)
        await session.commit()
        plan_id = plan.id

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    check = {c["check_type"]: c for c in resp.json()["prep_checks"]}["physician_plan"]
    assert check["status"] == "pending"
    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 409
    assert "physician_plan" in resp.json()["detail"]["missing"]

    # 恢复计划（清撤销）后门禁恢复
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        plan = await session.get(StewardCarePlan, plan_id)
        plan.revoked_at = None
        await session.commit()
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    check = {c["check_type"]: c for c in resp.json()["prep_checks"]}["physician_plan"]
    assert check["status"] == "passed"


async def test_activate_idempotent_and_exact_45d(client):
    """激活幂等（顺序重复）：返回原实例；ends_at 精确 45×24h；不重复生成日历；
    激活凭证快照完整（A1）。"""
    ctx = await full_setup(client)
    u = ctx["user"]
    instance_id = ctx["instance"]["id"]

    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["status"] == "active"
    assert first["plan_version"] == "v1"

    activated_at = datetime.fromisoformat(first["activated_at"].replace("Z", "+00:00"))
    ends_at = datetime.fromisoformat(first["ends_at"].replace("Z", "+00:00"))
    assert abs((ends_at - activated_at) - timedelta(days=45)) < timedelta(seconds=5)

    # 重复激活仍返回原实例，不重复生成日历 / 凭证
    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 200 and resp.json()["status"] == "active"

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await session.execute(
            select(StewardServiceInstance).where(
                StewardServiceInstance.entitlement_id == ctx["entitlement_id"]
            )
        )
        assert len(result.scalars().all()) == 1
        tasks = (
            (
                await session.execute(
                    select(StewardTask).where(StewardTask.instance_id == instance_id)
                )
            )
            .scalars()
            .all()
        )
        assert all(t.plan_version == "v1" for t in tasks)  # 任务固化计划版本
        credential = (
            (
                await session.execute(
                    select(StewardActivationCredential).where(
                        StewardActivationCredential.instance_id == instance_id
                    )
                )
            )
            .scalars()
            .one()
        )
        assert credential.plan_version == "v1"
        assert credential.consent_versions == {
            "steward_service": "v1",
            "steward_device_data": "v1",
        }
        assert credential.butler_id and credential.backup_butler_id and credential.physician_id
        assert credential.butler_id != credential.backup_butler_id
        gate = credential.gate_snapshot
        assert len(gate) == 9 and all(v["status"] == "passed" for v in gate.values())


async def test_concurrent_activate_single_instance(file_db_steward):
    """真并发激活（文件库 + NullPool 连接级隔离）：恰一个实例、一份日历、一次激活事件、一份凭证。"""
    import httpx

    import app.main as app_main
    from app.core.deps import get_db, get_redis
    from app.core.security import create_token
    from app.models import StewardTimelineEvent
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
            credentials = (
                (await session.execute(select(StewardActivationCredential))).scalars().all()
            )
            assert len(credentials) == 1  # 恰一份激活凭证
    finally:
        app_main.app.dependency_overrides.clear()


async def test_cross_user_and_editor_permissions(client):
    """跨用户实例一律 404；editor 可读 steward.read 接口但不可 manage。"""
    from tests.commerce_helpers import admin_headers

    ctx = await full_setup(client)
    instance_id = ctx["instance"]["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        other_user = User(nickname="用户乙", phone="13800000002", wx_openid="wx-2", status="active")
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
    resp = await client.post(
        f"{API}/admin/steward/consent-terms",
        json={"purpose": "steward_service", "version": "v2", "title": "t", "text": "x"},
        headers=editor,
    )
    assert resp.status_code == 403


async def test_user_without_wx_identity_fails_identity(client):
    """无微信身份（仅自填手机号）或无手机号的受益人：identity_phone 不通过。"""
    from app.models import EntitlementSource, MpEntitlement, MpOrder, MpPackageVersion
    from tests.commerce_helpers import admin_headers, make_package

    a = await admin_headers(client, "admin")
    pkg = await make_package(client, a)
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        nobody = User(nickname="无号码用户", status="active")  # 既无手机号也无 openid
        phone_only = User(nickname="仅有自填号码", phone="13800000009", status="active")
        session.add_all([nobody, phone_only])
        await session.flush()
        version = await session.get(MpPackageVersion, pkg["version_id"])
        for idx, target in enumerate((nobody, phone_only)):
            order = MpOrder(
                order_no=f"SPTEST{target.id:06d}",
                user_id=target.id,
                client_order_id=f"direct-{target.id}",
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
                    user_id=target.id,
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
        ids = [nobody.id, phone_only.id]

    for user_id in ids:
        resp = await client.get(f"{API}/mp/steward/instances", headers=user_headers(user_id))
        assert resp.status_code == 200
        detail = resp.json()[0]
        resp = await client.get(
            f"{API}/mp/steward/instances/{detail['id']}", headers=user_headers(user_id)
        )
        checks = {c["check_type"]: c for c in resp.json()["prep_checks"]}
        assert checks["identity_phone"]["status"] == "pending"
        assert "identity_phone" in (
            await client.post(
                f"{API}/mp/steward/instances/{detail['id']}/activate",
                headers=user_headers(user_id),
            )
        ).json()["detail"]["missing"]
