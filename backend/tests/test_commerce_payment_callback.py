"""微信支付回调闭环：真实签名/验签/解密、幂等重放、金额核对、对账查单。

验收口径（指导第五节）：
- 重放相同回调不重复发权益；
- 金额不符、签名错误或未知订单被拒并留下审计/事件记录；
- 支付成功以服务端回调为准，客户端不参与判定。
"""

import base64
import json
import secrets
import uuid
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import func, select

import app.main as app_main
from app.core.deps import get_db
from app.models import AuditLog, MpEntitlement, MpOrder, MpPayment, MpPaymentEvent
from app.services import wxpay_service
from tests.commerce_helpers import API, admin_headers, build_notify, make_package, user_headers, wxpay_env


async def _create_wxpay_order(client, package_id: int) -> dict:
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": package_id,
            "pay_type": "wxpay",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _success_payload(order_no: str, total: int, transaction_id: str | None = None) -> dict:
    return {
        "out_trade_no": order_no,
        "transaction_id": transaction_id or f"wx_tx_{secrets.token_hex(6)}",
        "trade_state": "SUCCESS",
        "amount": {"total": total, "currency": "CNY"},
    }


@pytest.mark.asyncio
async def test_notify_accepted_then_replay(client, wxpay_env):
    """验签通过 → paid + 恰一份权益；重放同一回调 → SUCCESS 且权益仍一份。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, price_cents=12800)
    order = await _create_wxpay_order(client, made["package_id"])

    payload = _success_payload(order["order_no"], 12800, "wx_tx_notify_001")
    body, headers = build_notify(wxpay_env, payload)
    resp = await client.post(
        f"{API}/pay/wxpay/notify", content=body, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == "SUCCESS"

    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "paid"
    assert detail["paid_at"] is not None
    assert detail["entitlement_id"] is not None

    # 重放：同一 transaction_id
    body2, headers2 = build_notify(wxpay_env, _success_payload(order["order_no"], 12800, "wx_tx_notify_001"))
    resp = await client.post(f"{API}/pay/wxpay/notify", content=body2, headers=headers2)
    assert resp.status_code == 200

    # 数据库断言走 API：权益恰一份、支付记录一笔 success、事件包含 accepted 与 replay
    ents = (await client.get(f"{API}/mp/entitlements", headers=user_headers())).json()
    mine = [e for e in ents["items"] if e["order_id"] == order["id"]]
    assert len(mine) == 1

    admin_detail = (
        await client.get(f"{API}/admin/mp/orders/{order['id']}", headers=admin)
    ).json()
    results = [e["result"] for e in admin_detail["events"]]
    assert "paid" in results
    assert "replay" in results
    txs = [p["transaction_id"] for p in admin_detail["payments"] if p["status"] == "success"]
    assert txs == ["wx_tx_notify_001"]
    audit_actions = (
        await client.get(
            f"{API}/admin/audit-logs?resource_type=payment&page_size=50", headers=admin
        )
    ).json()["items"]
    assert any(a["action"] == "commerce.payment.notify_accepted" for a in audit_actions)


@pytest.mark.asyncio
async def test_notify_bad_signature_rejected(client, wxpay_env):
    admin = await admin_headers(client)
    made = await make_package(client, admin, price_cents=6600)
    order = await _create_wxpay_order(client, made["package_id"])

    body, headers = build_notify(wxpay_env, _success_payload(order["order_no"], 6600))
    headers["Wechatpay-Signature"] = base64.b64encode(b"0" * 256).decode()  # 破坏签名
    resp = await client.post(f"{API}/pay/wxpay/notify", content=body, headers=headers)
    assert resp.status_code == 401

    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "pending_payment"

    # 过期时间戳同样拒绝
    body, headers = build_notify(
        wxpay_env, _success_payload(order["order_no"], 6600), timestamp=str(1000000000)
    )
    resp = await client.post(f"{API}/pay/wxpay/notify", content=body, headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_notify_amount_mismatch_and_unknown_order(client, wxpay_env):
    admin = await admin_headers(client)
    made = await make_package(client, admin, price_cents=8800)
    order = await _create_wxpay_order(client, made["package_id"])

    # 金额不符 → 409，不入账
    body, headers = build_notify(wxpay_env, _success_payload(order["order_no"], 1))
    resp = await client.post(f"{API}/pay/wxpay/notify", content=body, headers=headers)
    assert resp.status_code == 409
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "pending_payment"
    admin_detail = (
        await client.get(f"{API}/admin/mp/orders/{order['id']}", headers=admin)
    ).json()
    assert any(e["result"] == "amount_mismatch" for e in admin_detail["events"])

    # 未知订单 → 404 + 事件（order_id 为空）
    body, headers = build_notify(wxpay_env, _success_payload("SPNOTEXIST0001", 8800))
    resp = await client.post(f"{API}/pay/wxpay/notify", content=body, headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_notify_trade_state_closed(client, wxpay_env):
    """trade_state=CLOSED：登记支付失败，订单保持待支付。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, price_cents=3300)
    order = await _create_wxpay_order(client, made["package_id"])

    payload = {
        "out_trade_no": order["order_no"],
        "transaction_id": "wx_tx_closed_1",
        "trade_state": "CLOSED",
        "amount": {"total": 3300},
    }
    body, headers = build_notify(wxpay_env, payload)
    resp = await client.post(f"{API}/pay/wxpay/notify", content=body, headers=headers)
    assert resp.status_code == 200
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "pending_payment"


@pytest.mark.asyncio
async def test_notify_unconfigured_503(client):
    """未配置支付：回调一律 503 拒绝（wxpay_env 未启用时全局配置为空）。"""
    resp = await client.post(f"{API}/pay/wxpay/notify", content=b"{}", headers={})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_prepay_with_mocked_wechat(client, wxpay_env):
    """预下单：mock 微信 JSAPI 返回 prepay_id，接口返回 wx.requestPayment 参数。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, price_cents=5600)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "transactions/jsapi" in request.url.path
        auth = request.headers.get("Authorization", "")
        assert auth.startswith("WECHATPAY2-SHA256-RSA2048"), auth[:30]
        payload = json.loads(request.content)
        assert payload["out_trade_no"].startswith("SP")
        assert payload["amount"]["total"] == 5600
        return httpx.Response(200, json={"prepay_id": "wx_prepay_mock_1"})

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(handler))
    order = await _create_wxpay_order(client, made["package_id"])
    resp = await client.post(f"{API}/mp/orders/{order['id']}/prepay", headers=user_headers())
    assert resp.status_code == 200, resp.text
    params = resp.json()
    assert params["package"] == "prepay_id=wx_prepay_mock_1"
    assert params["signType"] == "RSA"
    assert set(params) == {"timeStamp", "nonceStr", "package", "signType", "paySign"}


@pytest.mark.asyncio
async def test_admin_sync_order_via_query(client, wxpay_env):
    """对账查单：mock 微信查单返回 SUCCESS，回调丢失时由 sync 入账。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, price_cents=7700)
    order = await _create_wxpay_order(client, made["package_id"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/v3/pay/transactions/out-trade-no/" in request.url.path
        return httpx.Response(
            200,
            json={
                "out_trade_no": order["order_no"],
                "transaction_id": "wx_tx_query_1",
                "trade_state": "SUCCESS",
                "amount": {"total": 7700},
            },
        )

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(handler))
    resp = await client.post(f"{API}/admin/mp/orders/{order['id']}/sync", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"
    assert resp.json()["entitlement_id"] is not None
    events = [e["event_type"] for e in resp.json()["events"]]
    assert "query_sync" in events

    # editor 无权限对账
    editor = await admin_headers(client, role="editor")
    resp = await client.post(f"{API}/admin/mp/orders/{order['id']}/sync", headers=editor)
    assert resp.status_code == 403
