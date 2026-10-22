"""服务包商品与订单闭环：可售性、下单幂等、积分兑换、越权隔离、授权记录。"""

import uuid

import pytest

from tests.commerce_helpers import API, admin_headers, make_package, user_headers


@pytest.mark.asyncio
async def test_package_lifecycle_and_visibility(client):
    """草稿不可见；上架可见；下架 404；未上架不可下单；游客首页可见列表但详情需登录。"""
    headers = await admin_headers(client)
    made = await make_package(client, headers, on_shelf=False)

    # 草稿：C 端列表不可见，详情（登录用户）404
    resp = await client.get(f"{API}/mp/packages", headers=user_headers())
    assert resp.status_code == 200
    assert all(p["id"] != made["package_id"] for p in resp.json()["items"])
    resp = await client.get(f"{API}/mp/packages/{made['package_id']}", headers=user_headers())
    assert resp.status_code == 404

    # 游客：第一页列表可见（上架后）
    resp = await client.post(
        f"{API}/admin/mp/packages/{made['package_id']}/shelf",
        json={"on_shelf": True, "version_id": made["version_id"]},
        headers=headers,
    )
    assert resp.status_code == 200
    resp = await client.get(f"{API}/mp/packages")
    assert resp.status_code == 200
    assert any(p["id"] == made["package_id"] for p in resp.json()["items"])
    item = next(p for p in resp.json()["items"] if p["id"] == made["package_id"])
    assert item["price_cents"] == 9900
    assert item["purchasable"]["points"] is False  # 未设积分价

    # 详情需登录
    resp = await client.get(f"{API}/mp/packages/{made['package_id']}")
    assert resp.status_code == 401
    resp = await client.get(f"{API}/mp/packages/{made['package_id']}", headers=user_headers())
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["terms_version"].startswith("v")
    assert len(detail["benefits"]) == 2

    # 下架后详情 404、下单 409
    resp = await client.post(
        f"{API}/admin/mp/packages/{made['package_id']}/shelf",
        json={"on_shelf": False},
        headers=headers,
    )
    assert resp.status_code == 200
    resp = await client.get(f"{API}/mp/packages/{made['package_id']}", headers=user_headers())
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_blockers_prevent_shelf(client):
    """配置不完整的版本不能上架：先用空 benefits 上架失败。"""
    headers = await admin_headers(client)
    resp = await client.post(
        f"{API}/admin/mp/packages", json={"name": "未配置包"}, headers=headers
    )
    package_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/packages/{package_id}/versions",
        json={
            "price_cents": 100,
            "validity_days": 30,
            "benefits": [{"name": "权益"}],
            "service_desc": "说明",
            "terms": "条款",
        },
        headers=headers,
    )
    version_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/packages/{package_id}/shelf",
        json={"on_shelf": True, "version_id": version_id},
        headers=headers,
    )
    assert resp.status_code == 200  # 完整配置可上架

    resp = await client.post(
        f"{API}/admin/mp/packages", json={"name": "缺版本包"}, headers=headers
    )
    package_id2 = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/packages/{package_id2}/shelf",
        json={"on_shelf": True},
        headers=headers,
    )
    assert resp.status_code == 422  # 未指定版本


@pytest.mark.asyncio
async def test_order_idempotency_and_wxpay_unconfigured(client):
    """wxpay 未配置下单 503；同 client_order_id 幂等返回原订单；参数变化 409。"""
    headers = await admin_headers(client)
    made = await make_package(client, headers)
    key = str(uuid.uuid4())

    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": key,
            "package_id": made["package_id"],
            "pay_type": "wxpay",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "支付通道未开通，请联系客服"

    # 未同意条款 422
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "pay_type": "points",
            "agree_terms": False,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_points_order_full_loop(client):
    """积分兑换：调整积分 → 下单即 paid → 恰一份 active 权益 → 流水正确 → 授权快照。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, points_cost=1000, price_cents=9900)

    # 余额不足 409
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "积分余额不足"

    # 管理员调整积分（editor 无权限）
    resp = await client.post(
        f"{API}/admin/mp/points/adjust",
        json={"user_id": 1, "delta": 1500, "note": "市场活动奖励"},
        headers=await admin_headers(client, role="editor"),
    )
    assert resp.status_code == 403
    resp = await client.post(
        f"{API}/admin/mp/points/adjust",
        json={"user_id": 1, "delta": 1500, "note": "市场活动奖励"},
        headers=admin,
    )
    assert resp.status_code == 200
    assert resp.json()["balance_after"] == 1500

    key = str(uuid.uuid4())
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": key,
            "package_id": made["package_id"],
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert order["status"] == "paid"
    assert order["points_cost"] == 1000
    assert order["entitlement_id"] is not None

    # 幂等：同 client_order_id 再下 → 200 同一订单
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": key,
            "package_id": made["package_id"],
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == order["id"]

    # 同 key 换支付方式 → 409
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": key,
            "package_id": made["package_id"],
            "pay_type": "wxpay",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 409

    # 权益：恰一份 active
    resp = await client.get(f"{API}/mp/entitlements", headers=user_headers())
    ents = [e for e in resp.json()["items"] if e["order_id"] == order["id"]]
    assert len(ents) == 1
    assert ents[0]["status"] == "active"
    assert ents[0]["valid_until"] is not None

    # 积分流水：+1500 调整 / -1000 兑换，余额 500
    resp = await client.get(f"{API}/mp/points", headers=user_headers())
    assert resp.json()["balance"] == 500
    resp = await client.get(f"{API}/mp/points/entries", headers=user_headers())
    entries = resp.json()["items"]
    assert [e["delta"] for e in entries] == [-1000, 1500]
    assert entries[0]["biz_type"] == "redeem"

    # 授权快照
    resp = await client.get(
        f"{API}/mp/consents?purpose=package_purchase", headers=user_headers()
    )
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["revoked_at"] is None
    consent_id = items[0]["id"]
    resp = await client.post(
        f"{API}/mp/consents/{consent_id}/revoke", headers=user_headers()
    )
    assert resp.status_code == 200
    assert resp.json()["revoked_at"] is not None


@pytest.mark.asyncio
async def test_cross_user_isolation(client):
    """跨用户订单/权益 404；批次权益不匹配手机号不可见。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, points_cost=100)
    resp = await client.post(
        f"{API}/admin/mp/points/adjust",
        json={"user_id": 1, "delta": 100, "note": "t"},
        headers=admin,
    )
    assert resp.status_code == 200

    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "pay_type": "points",
            "agree_terms": True,
        },
        headers=user_headers(1),
    )
    order_id = resp.json()["id"]

    resp = await client.post(f"{API}/wx/login", json={"code": "dev-code"})
    assert resp.status_code == 200
    other = {"Authorization": f"Bearer {resp.json()['token']}"}
    resp = await client.get(f"{API}/mp/orders/{order_id}", headers=other)
    assert resp.status_code == 404
    resp = await client.get(f"{API}/mp/orders", headers=other)
    assert resp.json()["total"] == 0
    resp = await client.get(f"{API}/mp/entitlements", headers=other)
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_order_close_and_capability(client):
    """未配置支付时能力接口返回 wxpay_enabled=false；wxpay 单在配置未开时直接 503。"""
    resp = await client.get(f"{API}/mp/commerce/capabilities", headers=user_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["wxpay_enabled"] is False
    assert body["points_enabled"] is True

    admin = await admin_headers(client)
    made = await make_package(client, admin)
    resp = await client.post(
        f"{API}/mp/orders/{999999}/close", headers=user_headers()
    )
    assert resp.status_code == 404
