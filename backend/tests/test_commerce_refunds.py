"""退款闭环：人工退款队列、积分返还、微信退款（mock）、权限隔离。"""

import json
import uuid

import httpx
import pytest

from app.services import wxpay_service
from tests.commerce_helpers import (
    API,
    admin_headers,
    make_package,
    user_headers,
    wxpay_env,
)


async def _paid_points_order(client, admin) -> dict:
    made = await make_package(client, admin, points_cost=800, price_cents=8800)
    resp = await client.post(
        f"{API}/admin/mp/points/adjust",
        json={"user_id": 1, "delta": 800, "note": "退款测试前置"},
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
        headers=user_headers(),
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_points_order_manual_refund_reverts(client):
    """积分单：仅允许人工退款；结论成功 → 订单 refunded、权益 void、积分返还。"""
    admin = await admin_headers(client)
    order = await _paid_points_order(client, admin)

    # editor 不能发起退款
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "manual", "reason": "用户申请"},
        headers=await admin_headers(client, role="editor"),
    )
    assert resp.status_code == 403

    # 积分单不允许 wxpay 渠道
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "wxpay", "reason": "用户申请"},
        headers=admin,
    )
    assert resp.status_code == 409

    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "manual", "reason": "用户申请退款"},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    refund = resp.json()
    assert refund["status"] == "pending"

    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "refund_pending"
    assert detail["refunds"][0]["status"] == "pending"

    # 退款队列可见
    resp = await client.get(f"{API}/admin/mp/refunds", headers=admin)
    assert any(r["id"] == refund["id"] for r in resp.json()["items"])

    # 登记成功结论
    resp = await client.post(
        f"{API}/admin/mp/refunds/{refund['id']}/resolve",
        json={"status": "success", "note": "线下已退"},
        headers=admin,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "refunded"
    ents = (await client.get(f"{API}/mp/entitlements", headers=user_headers())).json()["items"]
    mine = [e for e in ents if e["order_id"] == order["id"]]
    assert mine and mine[0]["status"] == "void"

    # 积分已返还（800 → 0 → 800）
    resp = await client.get(f"{API}/mp/points", headers=user_headers())
    assert resp.json()["balance"] == 800
    entries = (await client.get(f"{API}/mp/points/entries", headers=user_headers())).json()["items"]
    assert entries[0]["biz_type"] == "refund_revert"
    assert entries[0]["delta"] == 800

    # 重复登记结论 409
    resp = await client.post(
        f"{API}/admin/mp/refunds/{refund['id']}/resolve",
        json={"status": "failed", "note": "重复"},
        headers=admin,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_wxpay_refund_with_mock(client, wxpay_env):
    """微信退款：mock 退款接口受理 → processing；失败结论 → 订单 refund_failed。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, price_cents=4500)

    # 直接造一笔已支付订单：走 mock 回调最快
    from tests.commerce_helpers import build_notify

    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "pay_type": "wxpay",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    order = resp.json()
    payload = {
        "out_trade_no": order["order_no"],
        "transaction_id": "wx_tx_refund_1",
        "trade_state": "SUCCESS",
        "amount": {"total": 4500},
    }
    body, headers = build_notify(wxpay_env, payload)
    resp = await client.post(f"{API}/pay/wxpay/notify", content=body, headers=headers)
    assert resp.status_code == 200

    def handler(request: httpx.Request) -> httpx.Response:
        assert "refunds" in request.url.path
        payload = json.loads(request.content)
        assert payload["out_refund_no"].startswith("RF")
        return httpx.Response(200, json={"refund_id": "wx_rf_mock_1", "status": "PROCESSING"})

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(handler))
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "wxpay", "reason": "服务未履约"},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "processing"
    assert resp.json()["refund_no"].startswith("RF")

    # 登记失败结论 → 订单 refund_failed，可再次发起
    refund_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/refunds/{refund_id}/resolve",
        json={"status": "failed", "note": "银行渠道退回"},
        headers=admin,
    )
    assert resp.status_code == 200
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "refund_failed"

    # 超额退款 409
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "wxpay", "amount_cents": 4501, "reason": "超额"},
        headers=admin,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_refund_rejects_wrong_state(client, wxpay_env):
    """待支付订单不可退款。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin)
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "pay_type": "wxpay",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    order = resp.json()
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "manual", "reason": "想退未付订单"},
        headers=admin,
    )
    assert resp.status_code == 409
