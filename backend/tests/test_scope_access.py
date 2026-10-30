"""F02 岗位与服务实例访问范围（批次 B1）。

服务端统一计算健康正文可见范围：无绑定编辑一无所见；绑定管家只见本人指派实例；改派 / 人员
停用后同一令牌立即失权；特权管理跨范围读取留审计；筛选参数不能绕过。
"""

import pytest
from sqlalchemy import select

from app.models import AdminUser, AuditLog
from tests.commerce_helpers import API, admin_headers
from tests.steward_helpers import full_setup


async def _editor_id(client) -> int:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        return (await session.execute(select(AdminUser).where(AdminUser.username == "editor"))).scalar_one().id


async def _bind(client, a: dict, staff_id: int, admin_id: int) -> None:
    resp = await client.post(
        f"{API}/admin/steward/staff-links", json={"staff_id": staff_id, "admin_id": admin_id}, headers=a
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_unbound_editor_sees_no_health_content(client):
    """editor 持有四个 *.read 但无人员绑定：列表全空（含总数）、详情 403、筛选参数不能绕过、工作台为空。"""
    ctx = await full_setup(client)
    inst = ctx["instance"]
    e = await admin_headers(client, "editor")

    resp = await client.get(f"{API}/admin/steward/instances", headers=e)
    assert resp.status_code == 200 and resp.json()["total"] == 0 and resp.json()["items"] == []
    resp = await client.get(
        f"{API}/admin/steward/instances", params={"q": "1380000", "status": inst["status"]}, headers=e
    )
    assert resp.json()["total"] == 0
    assert (await client.get(f"{API}/admin/steward/instances/{inst['id']}", headers=e)).status_code == 403
    assert (await client.get(f"{API}/admin/steward/instances/{inst['id']}/timeline", headers=e)).status_code == 403
    assert (
        await client.get(f"{API}/admin/steward/instances/{inst['id']}/calendar", params={"month": "2026-10"}, headers=e)
    ).status_code == 403
    for path in ("/admin/outreach/intents", "/admin/devices/bindings", "/admin/devices/metrics",
                 "/admin/phone/orders", "/admin/phone/contacts", "/admin/steward/professional/orders"):
        resp = await client.get(f"{API}{path}", headers=e)
        assert resp.status_code == 200, (path, resp.text)
        assert resp.json()["total"] == 0, path
    assert (await client.get(f"{API}/admin/steward/care-plans", headers=e)).json() == []
    assert (await client.get(f"{API}/admin/steward/reports", headers=e)).json() == []
    wb = (await client.get(f"{API}/admin/steward/workbench", headers=e)).json()
    assert wb["instances"] == {} and wb["outreach_today"] == {} and wb["phone"] == {}
    # 非健康正文的配置类仍可读（人员 / 条款），范围规则只约束健康正文
    assert (await client.get(f"{API}/admin/steward/staff", headers=e)).status_code == 200


@pytest.mark.asyncio
async def test_bound_butler_sees_only_own_instance_and_loses_access_on_reassign(client):
    """甲管家绑定账号只见本人指派实例；改派给乙后同一令牌立即 403；人员停用同样失权。"""
    first = await full_setup(client)
    second = await full_setup(client)  # 同一用户第二份实例，另一组人员
    assert first["instance"]["id"] != second["instance"]["id"]
    a = first["admin"]
    editor_id = await _editor_id(client)
    await _bind(client, a, first["staff"]["butler"]["id"], editor_id)
    e = await admin_headers(client, "editor")

    resp = await client.get(f"{API}/admin/steward/instances", headers=e)
    assert resp.json()["total"] == 1 and resp.json()["items"][0]["id"] == first["instance"]["id"]
    assert (await client.get(f"{API}/admin/steward/instances/{first['instance']['id']}", headers=e)).status_code == 200
    assert (await client.get(f"{API}/admin/steward/instances/{second['instance']['id']}", headers=e)).status_code == 403
    # 按实例筛选乙实例的工单 / 触达 / 电话也不可见
    resp = await client.get(
        f"{API}/admin/outreach/intents", params={"instance_id": second["instance"]["id"]}, headers=e
    )
    assert resp.json()["total"] == 0

    # 改派：甲 → 乙（乙为第二实例的管家），甲所绑账号立即失权
    resp = await client.post(
        f"{API}/admin/steward/instances/{first['instance']['id']}/assign",
        json={"butler_id": second["staff"]["butler"]["id"]},
        headers=a,
    )
    assert resp.status_code == 200, resp.text
    assert (await client.get(f"{API}/admin/steward/instances/{first['instance']['id']}", headers=e)).status_code == 403
    assert (await client.get(f"{API}/admin/steward/instances", headers=e)).json()["total"] == 0

    # 改回甲，再把甲停用：绑定仍在但人员停用 → 失权
    resp = await client.post(
        f"{API}/admin/steward/instances/{first['instance']['id']}/assign",
        json={"butler_id": first["staff"]["butler"]["id"]},
        headers=a,
    )
    assert resp.status_code == 200, resp.text
    assert (await client.get(f"{API}/admin/steward/instances/{first['instance']['id']}", headers=e)).status_code == 200
    resp = await client.patch(
        f"{API}/admin/steward/staff/{first['staff']['butler']['id']}", json={"status": "inactive"}, headers=a
    )
    assert resp.status_code == 200
    assert (await client.get(f"{API}/admin/steward/instances/{first['instance']['id']}", headers=e)).status_code == 403


@pytest.mark.asyncio
async def test_privileged_cross_scope_read_is_audited(client):
    """admin（特权）无绑定也可读详情，但跨范围读取写审计 steward.scope.cross_read；列表不记审计。"""
    ctx = await full_setup(client)
    a = ctx["admin"]
    inst_id = ctx["instance"]["id"]

    async def cross_reads() -> int:
        async with client.session_factory() as session:  # type: ignore[attr-defined]
            rows = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "steward.scope.cross_read",
                        AuditLog.resource_type == "steward_service_instance",
                        AuditLog.resource_id == inst_id,
                    )
                )
            ).scalars().all()
            return len(rows)

    base = await cross_reads()
    assert (await client.get(f"{API}/admin/steward/instances", headers=a)).json()["total"] >= 1
    assert await cross_reads() == base
    assert (await client.get(f"{API}/admin/steward/instances/{inst_id}", headers=a)).status_code == 200
    assert await cross_reads() == base + 1
