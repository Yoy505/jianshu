"""工作二切片 C · 设备云数据用例（契约 docs/api.md 第五部分 · 八）。

覆盖：厂商/绑定与跨用户归属冲突、同步前置检查（无适配器 blocked / 授权撤回 blocked /
解绑 blocked）、去重与修订、质量策略（错人拒绝 / 无效 / 晚到 / 复核解除）、趋势不填 0
与缺口、游标封锁与重置、门禁自动化（device_paired / first_valid_data）、权限、beat 注册。
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import DeviceMetric, DeviceSyncCursor, StewardPrepCheck
from app.services import device_service as ds
from tests.commerce_helpers import API
from tests.device_helpers import (
    DEVICE_MODEL,
    FailingAdapter,
    ListAdapter,
    SYNTH_VENDOR,
    USER1_KEY,
    ensure_device_facts,
    make_record,
)
from tests.steward_helpers import full_setup

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_adapters():
    """模块级适配器注册表跨测试隔离：每用例前后清空（生产该表恒空）。"""
    ds.clear_adapters()
    yield
    ds.clear_adapters()


async def _vendor_and_binding(client, a, *, user_id: int = 1, sn: str | None = None):
    resp = await client.post(f"{API}/admin/devices/vendors", json=SYNTH_VENDOR, headers=a)
    assert resp.status_code in (201, 409)
    resp = await client.get(f"{API}/admin/devices/vendors", headers=a)
    vendor = [v for v in resp.json() if v["code"] == SYNTH_VENDOR["code"]][0]
    resp = await client.post(
        f"{API}/admin/devices/bindings",
        json={
            "user_id": user_id,
            "vendor_id": vendor["id"],
            "device_model": DEVICE_MODEL,
            "device_sn": sn or f"SN-{datetime.now(timezone.utc).strftime('%H%M%S')}-{user_id}",
        },
        headers=a,
    )
    return vendor, resp


async def _sync(client, binding_id: int):
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        return await ds.sync_binding(session, binding_id)


async def _metrics(client, binding_id: int) -> list[DeviceMetric]:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        rows = (
            (
                await session.execute(
                    select(DeviceMetric).where(DeviceMetric.binding_id == binding_id)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def test_binding_conflict_and_rebind(client):
    """同一设备同时只能有一个 active 绑定（跨用户归属校验）；解绑后可换绑。"""
    ctx = await full_setup(client)
    a = ctx["admin"]
    resp = await client.get(f"{API}/mp/steward/devices", headers=ctx["user"])
    existing_sn = resp.json()["binding"]["device_sn"]  # 掩码序列号仅尾 4 位可读

    # 同一设备（同 SN）重复绑定 → 409（不论归属）
    vendor, resp = await _vendor_and_binding(client, a, user_id=1, sn="SN-C1")
    resp2 = await client.post(
        f"{API}/admin/devices/bindings",
        json={"user_id": 1, "vendor_id": vendor["id"], "device_model": DEVICE_MODEL, "device_sn": "SN-C1"},
        headers=a,
    )
    assert resp2.status_code == 409
    await client.post(f"{API}/admin/devices/bindings/{resp.json()['id']}/unbind", json={"reason": "测试清理"}, headers=a)

    # 不同 SN 是另一台设备 → 201（同一用户多设备解绑史可见，active 唯一按设备计）
    vendor, resp = await _vendor_and_binding(client, a, user_id=1, sn="SN-C2")
    assert resp.status_code == 201
    binding_id = resp.json()["id"]

    # 跨用户：其他用户绑定同一设备 → 409
    from app.models import User

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        other = User(nickname="用户乙", phone="13800000002", wx_openid="wx-c2", status="active")
        session.add(other)
        await session.commit()
        other_id = other.id
    resp = await client.post(
        f"{API}/admin/devices/bindings",
        json={"user_id": other_id, "vendor_id": vendor["id"], "device_model": DEVICE_MODEL, "device_sn": "SN-C2"},
        headers=a,
    )
    assert resp.status_code == 409

    # 解绑后换绑到另一用户 → 成功；历史行保留
    resp = await client.post(f"{API}/admin/devices/bindings/{binding_id}/unbind", json={"reason": "设备转交"}, headers=a)
    assert resp.status_code == 200 and resp.json()["status"] == "unbound"
    resp = await client.post(
        f"{API}/admin/devices/bindings",
        json={"user_id": other_id, "vendor_id": vendor["id"], "device_model": DEVICE_MODEL, "device_sn": "SN-C2"},
        headers=a,
    )
    assert resp.status_code == 201


async def test_sync_blocked_without_adapter_and_consent(client):
    """无适配器 / 授权撤回 → blocked 留痕；不伪造任何数据。"""
    ctx = await full_setup(client, with_device=False)
    a, u = ctx["admin"], ctx["user"]
    vendor, resp = await _vendor_and_binding(client, a, user_id=1, sn="SN-B1")
    assert resp.status_code == 201
    binding_id = resp.json()["id"]

    # 未注册适配器：blocked 待配置，无数据入库
    result = await _sync(client, binding_id)
    assert result["status"] == "blocked" and "待配置" in result["detail"]
    assert not await _metrics(client, binding_id)

    # 授权撤回：即便注册了适配器也 blocked
    ds.register_adapter(SYNTH_VENDOR["code"], ListAdapter([[make_record("r-1")]]))
    resp = await client.get(f"{API}/mp/consents", params={"purpose": "steward_device_data"}, headers=u)
    consent_id = resp.json()["items"][0]["id"]
    await client.post(f"{API}/mp/consents/{consent_id}/revoke", headers=u)
    result = await _sync(client, binding_id)
    assert result["status"] == "blocked" and "授权" in result["detail"]

    # 重新授权后可同步
    terms = await client.get(f"{API}/mp/steward/consent-terms", headers=u)
    version = [t for t in terms.json() if t["purpose"] == "steward_device_data"][0]["version"]
    await client.post(
        f"{API}/mp/steward/consents", json={"purpose": "steward_device_data", "version": version}, headers=u
    )
    result = await _sync(client, binding_id)
    assert result["status"] == "success" and result["stored"] == 1


async def test_dedup_and_revision(client):
    """重复拉取跳过；厂商更新时间前进 → 合法修订（revision+1）；趋势取最新。"""
    ctx = await full_setup(client, with_device=False)
    a = ctx["admin"]
    vendor, resp = await _vendor_and_binding(client, a, user_id=1, sn="SN-D1")
    binding_id = resp.json()["id"]
    now = datetime.now(timezone.utc)

    ds.register_adapter(SYNTH_VENDOR["code"], ListAdapter([
        [make_record("rec-1", metrics=[("sleep_duration", 400)], updated_at=now)],
        [make_record("rec-1", metrics=[("sleep_duration", 400)], updated_at=now)],  # 重复
        [make_record("rec-1", metrics=[("sleep_duration", 430)], updated_at=now + timedelta(minutes=5))],  # 修订
    ]))
    first = await _sync(client, binding_id)
    assert first["stored"] == 1
    second = await _sync(client, binding_id)
    assert second["stored"] == 0 and second["duplicates"] == 1  # 重复跳过
    third = await _sync(client, binding_id)
    assert third["revisions"] == 1 and third["stored"] == 1

    rows = await _metrics(client, binding_id)
    assert len(rows) == 2  # 原始 + 修订，历史保留
    assert {r.revision for r in rows} == {1, 2}

    resp = await client.get(
        f"{API}/mp/steward/devices/trend",
        params={"metric": "sleep_duration", "days": 7},
        headers=ctx["user"],
    )
    assert resp.status_code == 200
    assert resp.json()["series"] == [430.0]  # 趋势取最新修订


async def test_quality_policy_wrong_user_invalid_late(client):
    """错人拒绝入库；无效与晚到入库标记不入趋势；晚到复核解除后入趋势。"""
    ctx = await full_setup(client, with_device=False)
    a = ctx["admin"]
    vendor, resp = await _vendor_and_binding(client, a, user_id=1, sn="SN-Q1")
    binding_id = resp.json()["id"]

    ds.register_adapter(SYNTH_VENDOR["code"], ListAdapter([[
        make_record("q-wrong", user_key="someone-else", metrics=[("sleep_duration", 400)]),
        make_record("q-invalid", metrics=[("sleep_score", 999)]),          # 超出 0–100
        make_record("q-late", day_offset=-30, metrics=[("sleep_duration", 380)]),  # 晚于 7 天窗口
        make_record("q-valid", metrics=[("sleep_duration", 410)]),
    ]]))
    result = await _sync(client, binding_id)
    assert result["status"] == "partial"
    assert result["wrong_user"] == 1 and result["invalid"] == 1 and result["late"] == 1
    assert result["stored"] == 3  # 无效/晚到仍入库留痕，错人不入库

    rows = {r.vendor_record_id: r for r in await _metrics(client, binding_id)}
    assert set(rows) == {"q-invalid", "q-late", "q-valid"}  # 错人未落库
    assert rows["q-invalid"].quality == "invalid_value"
    assert rows["q-late"].quality == "late"

    # 趋势只含有效；无 0 填充
    resp = await client.get(
        f"{API}/mp/steward/devices/trend", params={"metric": "sleep_duration", "days": 60},
        headers=ctx["user"],
    )
    body = resp.json()
    assert body["series"] == [410.0]
    assert "不作为医学诊断" in body["quality_note"]

    # 晚到复核解除 → 入趋势（留痕）
    resp = await client.post(
        f"{API}/admin/devices/metrics/{rows['q-late'].id}/review",
        json={"reason": "厂商补传确认，数据完整可信"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["quality"] == "reviewed"
    resp = await client.get(
        f"{API}/mp/steward/devices/trend", params={"metric": "sleep_duration", "days": 60},
        headers=ctx["user"],
    )
    assert len(resp.json()["series"]) == 2

    # 非 late 复核 → 409
    resp = await client.post(
        f"{API}/admin/devices/metrics/{rows['q-valid'].id}/review", json={"reason": "试图改正"}, headers=a
    )
    assert resp.status_code == 409


async def test_trend_gaps_no_zero_fill(client):
    """缺口日如实返回 gaps，不用 0 填补 series。"""
    ctx = await full_setup(client, with_device=False)
    a = ctx["admin"]
    vendor, resp = await _vendor_and_binding(client, a, user_id=1, sn="SN-G1")
    binding_id = resp.json()["id"]
    ds.register_adapter(SYNTH_VENDOR["code"], ListAdapter([[
        make_record("g-1", day_offset=-3, metrics=[("sleep_duration", 400)]),
        make_record("g-2", day_offset=-1, metrics=[("sleep_duration", 420)]),
    ]]))
    await _sync(client, binding_id)

    resp = await client.get(
        f"{API}/mp/steward/devices/trend", params={"metric": "sleep_duration", "days": 14},
        headers=ctx["user"],
    )
    body = resp.json()
    assert len(body["dates"]) == 2 and body["series"] == [400.0, 420.0]
    assert len(body["gaps"]) >= 1  # 中间缺口日如实列出
    assert all(v != 0 for v in body["series"])


async def test_cursor_blocked_and_reset(client):
    """连续失败达上限 → blocked；人工重置（可回退游标）后恢复。"""
    ctx = await full_setup(client, with_device=False)
    a = ctx["admin"]
    vendor, resp = await _vendor_and_binding(client, a, user_id=1, sn="SN-F1")
    binding_id = resp.json()["id"]

    ds.register_adapter(SYNTH_VENDOR["code"], FailingAdapter())
    from app.core.config import settings

    saved = settings.STEWARD_DEVICE_MAX_SYNC_FAILURES
    settings.STEWARD_DEVICE_MAX_SYNC_FAILURES = 3
    try:
        for _ in range(3):
            result = await _sync(client, binding_id)
            assert result["status"] == "failed"
        async with client.session_factory() as session:  # type: ignore[attr-defined]
            cursor = (
                await session.execute(
                    select(DeviceSyncCursor).where(DeviceSyncCursor.binding_id == binding_id)
                )
            ).scalar_one()
            assert cursor.status == "blocked"
        # blocked 后再同步 → 直接 blocked（不调适配器）
        result = await _sync(client, binding_id)
        assert result["status"] == "blocked" and "封锁" in result["detail"]
    finally:
        settings.STEWARD_DEVICE_MAX_SYNC_FAILURES = saved

    # 人工重置 + 回退游标 → 恢复成功
    ds.register_adapter(SYNTH_VENDOR["code"], ListAdapter([[make_record("f-1")]]))
    resp = await client.post(
        f"{API}/admin/devices/bindings/{binding_id}/cursor/reset",
        json={"reason": "厂商接口已恢复", "back": True},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["cursor"]["status"] == "idle"
    result = await _sync(client, binding_id)
    assert result["status"] == "success"


async def test_unbind_blocks_sync_and_freezes_trend(client):
    """解绑：同步 blocked、趋势不再可见（active 绑定口径）；旧数据后台可查。"""
    ctx = await full_setup(client)  # 含设备事实
    a, u = ctx["admin"], ctx["user"]
    resp = await client.get(f"{API}/mp/steward/devices", headers=u)
    assert resp.status_code == 200
    binding = resp.json()["binding"]
    assert binding is not None and binding["status"] == "active"

    resp = await client.post(
        f"{API}/mp/steward/devices/bindings/{binding['id']}/unbind",
        json={"reason": "设备损坏返厂"},
        headers=u,
    )
    assert resp.status_code == 200

    result = await _sync(client, binding["id"])
    assert result["status"] == "blocked" and "解绑" in result["detail"]
    resp = await client.get(
        f"{API}/mp/steward/devices/trend", params={"metric": "sleep_duration"}, headers=u
    )
    assert resp.json()["dates"] == []  # 无 active 绑定：趋势为空（不报错）

    resp = await client.get(
        f"{API}/admin/devices/metrics", params={"binding_id": binding["id"]}, headers=a
    )
    assert resp.json()["total"] >= 1  # 旧数据保留可追溯


async def test_prep_gates_automated_by_device_facts(client):
    """切片 C 门禁自动化：无绑定两项 pending；绑定后 paired 过、有数据后 first_valid 过。"""
    ctx = await full_setup(client, with_device=False, with_release=False)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    checks = {c["check_type"]: c for c in resp.json()["prep_checks"]}
    assert checks["device_paired"]["status"] == "pending"
    assert checks["first_valid_data"]["status"] == "pending"
    # 自动项人工置过 → 409
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/prep-checks/device_paired/pass",
        json={"evidence": "试图人工通过"},
        headers=a,
    )
    assert resp.status_code == 409

    # 建绑定 + 首条有效数据 → 两项自动通过；随后激活成功（真实数据事实过门禁）
    await ensure_device_facts(client, a)
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    checks = {c["check_type"]: c for c in resp.json()["prep_checks"]}
    assert checks["device_paired"]["status"] == "passed"
    assert checks["first_valid_data"]["status"] == "passed"

    from tests.steward_helpers import create_pilot_release

    await create_pilot_release(client, a, ctx["package"])
    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 200, resp.text


async def test_sync_logs_and_permissions(client):
    """同步日志与失败列表可查；editor 只读 403；beat 注册。"""
    ctx = await full_setup(client)
    a = ctx["admin"]
    resp = await client.get(f"{API}/admin/devices/sync-logs", headers=a)
    assert resp.status_code == 200 and resp.json()["total"] >= 1

    from tests.commerce_helpers import admin_headers

    editor = await admin_headers(client, "editor")
    resp = await client.get(f"{API}/admin/devices/bindings", headers=editor)
    assert resp.status_code == 200
    resp = await client.post(
        f"{API}/admin/devices/vendors", json={"code": "x", "name": "越权"}, headers=editor
    )
    assert resp.status_code == 403

    from app.workers.celery_app import celery_app
    from app.workers.tasks import device_sync_sweep

    assert celery_app.conf.beat_schedule["device-sync-sweep"]["task"] == "app.workers.tasks.device_sync_sweep"
    assert device_sync_sweep.name == "app.workers.tasks.device_sync_sweep"
