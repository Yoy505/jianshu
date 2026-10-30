"""F04 停用医师失权 · 可信签发身份链（批次 B2）。

工单完成与更正报告共用 require_physician_identity：当前指派医师存在 → 角色医师 → 在职 →
录入账号绑定该医师；条件更新再次锁定"仍指派给该医师且在职"，覆盖停用 / 转交与完成并发。
"""

import pytest
from sqlalchemy import select, update

from app.models import AdminUser, StewardServiceInstance, StewardStaff
from app.services import professional_service as prof
from tests.commerce_helpers import API
from tests.steward_helpers import create_staff, full_setup


async def _activate(client) -> dict:
    ctx = await full_setup(client)
    resp = await client.post(f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"])
    assert resp.status_code == 200, resp.text
    ctx["instance"] = resp.json()
    return ctx


async def _admin_id(client, username: str = "admin") -> int:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        return (await session.execute(select(AdminUser).where(AdminUser.username == username))).scalar_one().id


async def _bind(client, a, staff_id: int, admin_id: int) -> int:
    resp = await client.post(f"{API}/admin/steward/staff-links", json={"staff_id": staff_id, "admin_id": admin_id}, headers=a)
    assert resp.status_code == 201, resp.text
    links = (await client.get(f"{API}/admin/steward/staff-links", headers=a)).json()
    return next(r["id"] for r in links if r["staff_id"] == staff_id and r["admin_id"] == admin_id)


async def _order(client, a, instance_id: int, physician_id: int) -> int:
    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": instance_id, "order_type": "data_quality", "title": "数据质量检查",
              "priority": "normal", "physician_id": physician_id},
        headers=a,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _complete(client, a, order_id: int):
    return await client.post(f"{API}/admin/steward/professional/orders/{order_id}/complete",
                             json={"result": "检查完成"}, headers=a)


async def _force_staff_status(client, staff_id: int, status: str) -> None:
    """绕过 API 的 409 前置（模拟并发 / 历史数据）直接改人员状态。"""
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        await session.execute(update(StewardStaff).where(StewardStaff.id == staff_id).values(status=status))
        await session.commit()


@pytest.mark.asyncio
async def test_transfer_revoked_binding_and_deactivation_all_block_completion(client):
    ctx = await _activate(client)
    a = ctx["admin"]
    inst_id = ctx["instance"]["id"]
    admin_id = await _admin_id(client)
    phys_a = ctx["staff"]["physician"]
    phys_b = await create_staff(client, a, "physician", "接收医师乙")
    phys_c = await create_staff(client, a, "physician", "医师丙丙")

    # 1) 转交后旧医师（已绑定）失权，接收医师绑定后可完成
    await _bind(client, a, phys_a["id"], admin_id)
    order = await _order(client, a, inst_id, phys_a["id"])
    resp = await client.post(f"{API}/admin/steward/professional/orders/{order}/transfer",
                             json={"to_physician_id": phys_b["id"], "reason": "休假"}, headers=a)
    assert resp.status_code == 200
    resp = await _complete(client, a, order)
    assert resp.status_code == 403 and "绑定当前指派医师" in resp.json()["detail"]
    link_b = await _bind(client, a, phys_b["id"], admin_id)
    assert (await _complete(client, a, order)).status_code == 200

    # 2) 撤销绑定后不能再完成
    order2 = await _order(client, a, inst_id, phys_b["id"])
    assert (await client.delete(f"{API}/admin/steward/staff-links/{link_b}", headers=a)).status_code == 200
    assert (await _complete(client, a, order2)).status_code == 403

    # 3) 医师停用（绕过 API 前置直接置 inactive，模拟并发或历史数据）→ 403
    await _bind(client, a, phys_c["id"], admin_id)
    order3 = await _order(client, a, inst_id, phys_c["id"])
    await _force_staff_status(client, phys_c["id"], "inactive")
    resp = await _complete(client, a, order3)
    assert resp.status_code == 403 and "已停用" in resp.json()["detail"]
    # 恢复在职后正常人员仍可操作
    await _force_staff_status(client, phys_c["id"], "active")
    assert (await _complete(client, a, order3)).status_code == 200

    # 4) 通过 API 停用名下仍有未结工单的医师 → 409（须先转交）
    order4 = await _order(client, a, inst_id, phys_c["id"])
    resp = await client.patch(f"{API}/admin/steward/staff/{phys_c['id']}", json={"status": "inactive"}, headers=a)
    assert resp.status_code == 409
    resp = await client.post(f"{API}/admin/steward/professional/orders/{order4}/cancel",
                             json={"reason": "测试收尾"}, headers=a)
    assert resp.status_code == 200
    resp = await client.patch(f"{API}/admin/steward/staff/{phys_c['id']}", json={"status": "inactive"}, headers=a)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_conditional_update_guards_deactivation_race(client, monkeypatch):
    """核验通过后、写入前医师被停用：条件更新 rowcount=0 → 409，不会写出已完成事实。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    admin_id = await _admin_id(client)
    phys = await create_staff(client, a, "physician", "并发医师")
    await _bind(client, a, phys["id"], admin_id)
    order = await _order(client, a, ctx["instance"]["id"], phys["id"])

    real_check = prof.require_physician_identity

    async def check_then_deactivate(db, admin, physician_id, *, action):
        staff = await real_check(db, admin, physician_id, action=action)
        await _force_staff_status(client, physician_id, "inactive")
        return staff

    monkeypatch.setattr(prof, "require_physician_identity", check_then_deactivate)
    resp = await _complete(client, a, order)
    assert resp.status_code == 409
    detail = (await client.get(f"{API}/admin/steward/professional/orders/{order}", headers=a)).json()
    assert detail["status"] != "done" and detail["result"] is None


@pytest.mark.asyncio
async def test_correction_report_uses_same_identity_chain(client):
    ctx = await _activate(client)
    a = ctx["admin"]
    inst_id = ctx["instance"]["id"]
    phys = ctx["staff"]["physician"]
    admin_id = await _admin_id(client)
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        await session.execute(
            update(StewardServiceInstance).where(StewardServiceInstance.id == inst_id).values(status="closed")
        )
        await session.commit()
    body = {"instance_id": inst_id, "title": "更正", "content": "更正内容", "basis": "补充资料"}

    resp = await client.post(f"{API}/admin/steward/reports", json=body, headers=a)
    assert resp.status_code == 403 and "运营账号不能代医师签发" in resp.json()["detail"]
    await _bind(client, a, phys["id"], admin_id)
    await _force_staff_status(client, phys["id"], "inactive")
    resp = await client.post(f"{API}/admin/steward/reports", json=body, headers=a)
    assert resp.status_code == 403 and "已停用" in resp.json()["detail"]
    await _force_staff_status(client, phys["id"], "active")
    resp = await client.post(f"{API}/admin/steward/reports", json=body, headers=a)
    assert resp.status_code == 409 and "尚无原报告" in resp.json()["detail"]  # 身份链通过，卡在业务前置
