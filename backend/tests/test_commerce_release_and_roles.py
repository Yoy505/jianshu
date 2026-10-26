"""批次 2 回归：R7 购买条款版本、R8 开售放行、I1 岗位权限矩阵。"""

import uuid

import pytest

from tests.commerce_helpers import API, admin_headers, make_package, user_headers


@pytest.mark.asyncio
async def test_r8_not_released_blocks_purchase_and_shows_reason(client):
    """未放行：C 端可购原因为「未完成开售放行」，下单 409。"""
    admin = await admin_headers(client)
    # 手工建包不自动放行（复用 make_package 的版本流程但撤销放行不可行——直接建）
    resp = await client.post(
        f"{API}/admin/mp/packages", json={"name": "未放行服务包"}, headers=admin
    )
    package_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/packages/{package_id}/versions",
        json={
            "price_cents": 3000,
            "validity_days": 30,
            "benefits": [{"name": "健康管家"}],
            "service_desc": "说明",
            "terms": "条款",
        },
        headers=admin,
    )
    version_id = resp.json()["id"]
    await client.post(
        f"{API}/admin/mp/packages/{package_id}/shelf",
        json={"on_shelf": True, "version_id": version_id},
        headers=admin,
    )

    resp = await client.get(f"{API}/mp/packages", headers=user_headers())
    item = next(p for p in resp.json()["items"] if p["id"] == package_id)
    assert item["purchasable"]["wxpay"] is False
    assert "未完成开售放行" in item["purchasable"]["reasons"]

    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": package_id,
            "version_id": version_id,
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 409
    assert "开售放行" in resp.json()["detail"]

    # 放行后可购（积分路径同样由放行判定，不依赖微信配置）
    resp = await client.post(
        f"{API}/admin/mp/packages/{package_id}/releases",
        json={"version_id": version_id, "env": "all", "basis": "商品/售价/退款规则已确认，履约团队到位（依据：业务放行单 2026-001）"},
        headers=admin,
    )
    assert resp.status_code == 201
    release_id = resp.json()["id"]

    resp = await client.get(f"{API}/mp/packages/{package_id}", headers=user_headers())
    assert "未完成开售放行" not in resp.json()["purchasable"]["reasons"]

    # 撤销后回到不可售
    resp = await client.post(
        f"{API}/admin/mp/releases/{release_id}/revoke",
        json={"reason": "履约团队变更，暂停销售"},
        headers=admin,
    )
    assert resp.status_code == 200
    resp = await client.get(f"{API}/mp/packages/{package_id}", headers=user_headers())
    assert "未完成开售放行" in resp.json()["purchasable"]["reasons"]


@pytest.mark.asyncio
async def test_r7_version_conflict_requires_reconfirmation(client):
    """下单版本与当前上架版本不一致 → 409 要求重新确认；积分单授权记录带来源。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, points_cost=100)
    await client.post(
        f"{API}/admin/mp/points/adjust",
        json={"user_id": 1, "delta": 200, "note": "R7 前置"},
        headers=admin,
    )

    # 用旧/错误 version_id → 409
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "version_id": made["version_id"] + 999,
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 409
    assert "重新确认" in resp.json()["detail"]

    # 上新版本并上架：旧版本 id 下单 → 409
    resp = await client.post(
        f"{API}/admin/mp/packages/{made['package_id']}/versions",
        json={
            "price_cents": 12000,
            "points_cost": 120,
            "validity_days": 45,
            "benefits": [{"name": "健康管家 v2"}],
            "service_desc": "说明 v2",
            "terms": "条款 v2",
        },
        headers=admin,
    )
    v2 = resp.json()["id"]
    await client.post(
        f"{API}/admin/mp/packages/{made['package_id']}/shelf",
        json={"on_shelf": True, "version_id": v2},
        headers=admin,
    )
    await client.post(
        f"{API}/admin/mp/packages/{made['package_id']}/releases",
        json={"version_id": v2, "env": "all", "basis": "v2 放行"},
        headers=admin,
    )
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 409

    # 用当前版本成功下单：授权快照为 v2 条款且带来源
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "version_id": v2,
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 201
    resp = await client.get(
        f"{API}/mp/consents?purpose=package_purchase", headers=user_headers()
    )
    consent = resp.json()["items"][0]
    assert consent["version"] == "v2"
    assert consent["text"] == "条款 v2"
    assert consent["source"] == "order_confirm"


@pytest.mark.asyncio
async def test_r8_release_permissions_and_duplicate(client):
    """editor/finance 不能放行；同版本同环境重复放行 409。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin)  # 已有 env=all 放行
    for role in ("editor", "finance"):
        resp = await client.post(
            f"{API}/admin/mp/packages/{made['package_id']}/releases",
            json={"version_id": made["version_id"], "env": "prod", "basis": "越权放行"},
            headers=await admin_headers(client, role=role),
        )
        assert resp.status_code == 403, role

    resp = await client.post(
        f"{API}/admin/mp/packages/{made['package_id']}/releases",
        json={"version_id": made["version_id"], "env": "all", "basis": "重复"},
        headers=admin,
    )
    assert resp.status_code == 409
    # 另一环境可另行放行
    resp = await client.post(
        f"{API}/admin/mp/packages/{made['package_id']}/releases",
        json={"version_id": made["version_id"], "env": "prod", "basis": "生产放行"},
        headers=admin,
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_i1_finance_role_matrix(client):
    """finance：可查订单/退款/调积分；不可维护商品/批次确认/放行/查内容域。"""
    finance = await admin_headers(client, role="finance")
    editor = await admin_headers(client, role="editor")
    admin = await admin_headers(client)
    made = await make_package(client, admin, points_cost=100)
    await client.post(
        f"{API}/admin/mp/points/adjust",
        json={"user_id": 1, "delta": 100, "note": "I1 前置"},
        headers=finance,
    )
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    order_id = resp.json()["id"]

    # 财务可查订单 / 退款队列
    assert (await client.get(f"{API}/admin/mp/orders", headers=finance)).status_code == 200
    assert (await client.get(f"{API}/admin/mp/refunds", headers=finance)).status_code == 200
    resp = await client.post(
        f"{API}/admin/mp/orders/{order_id}/refund",
        json={"channel": "manual", "reason": "财务处理"},
        headers=finance,
    )
    assert resp.status_code == 200

    # 财务不可：商品维护 / 批次 / 放行 / 审计（各列表只读为 admin-token 通用，不在此列）
    for path, method in [
        (f"{API}/admin/mp/packages", "POST"),
        (f"{API}/admin/mp/batches/preview", "POST"),
        (f"{API}/admin/mp/entitlements", "GET"),
        (f"{API}/admin/audit-logs", "GET"),
    ]:
        resp = await client.request(method, path, headers=finance)
        assert resp.status_code == 403, path

    # editor 不可退款/调积分（既有矩阵回归）
    assert (
        await client.post(
            f"{API}/admin/mp/orders/{order_id}/refund",
            json={"channel": "manual", "reason": "越权"},
            headers=editor,
        )
    ).status_code == 403
    assert (
        await client.post(
            f"{API}/admin/mp/points/adjust",
            json={"user_id": 1, "delta": 1, "note": "越权"},
            headers=editor,
        )
    ).status_code == 403
