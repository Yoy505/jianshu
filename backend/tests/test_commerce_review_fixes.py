"""任务书 R1–R6 交易正确性回归（2026-10-22）。

R1 成功字段严格校验；R2 多次预下单唯一约束；R3/R4 退款可恢复可追溯；
R5 部分退款阻断与权益/积分一致性；R6 跨批次确认防重与权益事件留痕。
"""

import base64
import json
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from app.services import wxpay_service
from tests.commerce_helpers import (
    API,
    admin_headers,
    build_notify,
    make_package,
    user_headers,
    wxpay_env,
)


async def _wxpay_order(client, admin, price_cents=4500) -> dict:
    made = await make_package(client, admin, price_cents=price_cents)
    resp = await client.post(
        f"{API}/mp/orders",
        json={
            "client_order_id": str(uuid.uuid4()),
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "pay_type": "wxpay",
            "agree_terms": True,
        },
        headers=user_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _notify(client, env, payload) -> int:
    body, headers = build_notify(env, payload)
    resp = await client.post(f"{API}/pay/wxpay/notify", content=body, headers=headers)
    return resp.status_code


def _success(order_no, total, tx, currency="CNY", with_amount=True, with_tx=True):
    amount = {"total": total}
    if currency:
        amount["currency"] = currency
    payload = {
        "out_trade_no": order_no,
        "trade_state": "SUCCESS",
        "amount": amount,
    }
    if with_tx:
        payload["transaction_id"] = tx
    else:
        payload["transaction_id"] = None if not with_tx else tx
    if not with_amount:
        del payload["amount"]["total"]
    return payload


# ───────────────────────── R1 成功字段严格校验 ─────────────────────────


@pytest.mark.asyncio
async def test_r1_missing_or_invalid_success_fields_rejected(client, wxpay_env):
    admin = await admin_headers(client)
    order = await _wxpay_order(client, admin, 6600)

    # 缺交易号 / 缺金额 / 币种不符 / 金额为字符串：全部 422，不入账不发权益
    cases = [
        _success(order["order_no"], 6600, "wx_tx_a1", with_tx=False),
        _success(order["order_no"], 6600, "wx_tx_a2", with_amount=False),
        _success(order["order_no"], 6600, "wx_tx_a3", currency="USD"),
        {**_success(order["order_no"], 6600, "wx_tx_a4"), "amount": {"total": "6600", "currency": "CNY"}},
        _success(order["order_no"], 6600, "bad tx!#"),  # 交易号格式非法
    ]
    for payload in cases:
        assert await _notify(client, wxpay_env, payload) == 422, payload

    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "pending_payment"
    ents = (await client.get(f"{API}/mp/entitlements", headers=user_headers())).json()
    assert ents["total"] == 0
    admin_detail = (await client.get(f"{API}/admin/mp/orders/{order['id']}", headers=admin)).json()
    assert any(e["result"] == "invalid_success_fields" for e in admin_detail["events"])

    # 字段补齐后可正常入账（同订单恢复）
    assert await _notify(client, wxpay_env, _success(order["order_no"], 6600, "wx_tx_ok1")) == 200
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "paid"


@pytest.mark.asyncio
async def test_r1_currency_mismatch_via_query_sync(client, wxpay_env):
    """对账查单同样走严格校验：币种不符不入账。"""
    admin = await admin_headers(client)
    order = await _wxpay_order(client, admin, 3300)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "out_trade_no": order["order_no"],
                "transaction_id": "wx_tx_q_usd",
                "trade_state": "SUCCESS",
                "amount": {"total": 3300, "currency": "USD"},
            },
        )

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(handler))
    resp = await client.post(f"{API}/admin/mp/orders/{order['id']}/sync", headers=admin)
    assert resp.status_code == 422
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "pending_payment"


@pytest.mark.asyncio
async def test_r1_cross_order_transaction_reuse_rejected(client, wxpay_env):
    """同一微信交易号不能给第二张订单入账。"""
    admin = await admin_headers(client)
    order_a = await _wxpay_order(client, admin, 5000)
    order_b = await _wxpay_order(client, admin, 5000)
    shared_tx = "wx_tx_shared_1"

    assert await _notify(client, wxpay_env, _success(order_a["order_no"], 5000, shared_tx)) == 200
    assert (
        await _notify(client, wxpay_env, _success(order_b["order_no"], 5000, shared_tx)) == 409
    )
    detail_b = (await client.get(f"{API}/mp/orders/{order_b['id']}", headers=user_headers())).json()
    assert detail_b["status"] == "pending_payment"
    ents = (await client.get(f"{API}/mp/entitlements", headers=user_headers())).json()
    assert ents["total"] == 1  # 只有 A 的权益


def _sign_raw(env, body: bytes) -> dict:
    import time as _time

    ts = str(int(_time.time()))
    nonce = uuid.uuid4().hex
    message = f"{ts}\n{nonce}\n".encode() + body + b"\n"
    sig = base64.b64encode(
        env.platform_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    return {"Wechatpay-Timestamp": ts, "Wechatpay-Nonce": nonce, "Wechatpay-Signature": sig}


@pytest.mark.asyncio
async def test_r1_malformed_envelope_no_500(client, wxpay_env):
    """畸形回调（篡改密文=认证标签失败 / 完全非 JSON）→ 4xx，不是 500。"""
    # 篡改密文：签名对该体重算，验签通过但 GCM 认证失败 → 400
    body, _ = build_notify(wxpay_env, {"k": 1})
    envelope = json.loads(body)
    envelope["resource"]["ciphertext"] = base64.b64encode(b"tampered-data").decode()
    tampered = json.dumps(envelope).encode()
    resp = await client.post(
        f"{API}/pay/wxpay/notify", content=tampered, headers=_sign_raw(wxpay_env, tampered)
    )
    assert resp.status_code == 400

    # 完全非 JSON（签名正确）：解析失败 → 400
    raw = b"not-json"
    resp = await client.post(
        f"{API}/pay/wxpay/notify", content=raw, headers=_sign_raw(wxpay_env, raw)
    )
    assert resp.status_code == 400


# ───────────────────────── R2 多次预下单 ─────────────────────────


@pytest.mark.asyncio
async def test_r2_double_prepay_then_success_single_ledger(client, wxpay_env):
    """两轮预下单后支付成功：恰一条 success 支付记录（另一条 superseded），权益一份。"""
    admin = await admin_headers(client)
    order = await _wxpay_order(client, admin, 8800)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"prepay_id": "wx_prepay_r2"})

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(handler))
    for _ in range(2):
        resp = await client.post(f"{API}/mp/orders/{order['id']}/prepay", headers=user_headers())
        assert resp.status_code == 200

    assert await _notify(client, wxpay_env, _success(order["order_no"], 8800, "wx_tx_r2")) == 200
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "paid"

    admin_detail = (await client.get(f"{API}/admin/mp/orders/{order['id']}", headers=admin)).json()
    payments = admin_detail["payments"]
    assert len(payments) == 2
    success_rows = [p for p in payments if p["status"] == "success"]
    superseded = [p for p in payments if p["status"] == "failed" and p["fail_reason"] == "superseded"]
    assert len(success_rows) == 1
    assert success_rows[0]["transaction_id"] == "wx_tx_r2"
    assert len(superseded) == 1
    assert superseded[0]["transaction_id"] is None

    ents = (await client.get(f"{API}/mp/entitlements", headers=user_headers())).json()
    assert ents["total"] == 1

    # 重放同交易号仍幂等
    assert await _notify(client, wxpay_env, _success(order["order_no"], 8800, "wx_tx_r2")) == 200
    admin_detail = (await client.get(f"{API}/admin/mp/orders/{order['id']}", headers=admin)).json()
    assert len([p for p in admin_detail["payments"] if p["status"] == "success"]) == 1


# ───────────────────────── R3/R4 退款可恢复可追溯 ─────────────────────────


def _paid_wxpay_order_payload(order_no: str, total: int, tx: str) -> dict:
    return _success(order_no, total, tx)


@pytest.mark.asyncio
async def test_r3_wxpay_definite_rejection_recoverable(client, wxpay_env):
    """微信明确拒绝：退款 failed + 订单 refund_failed，可重新发起并成功。"""
    admin = await admin_headers(client)
    order = await _wxpay_order(client, admin, 7200)
    assert await _notify(client, wxpay_env, _paid_wxpay_order_payload(order["order_no"], 7200, "wx_tx_r3")) == 200

    # 第一轮：微信明确返回错误
    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "REFUND_ERROR", "message": "余额不足"})

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(reject))
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "wxpay", "reason": "服务未履约"},
        headers=admin,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "refund_failed"  # 可恢复，不再是永久 refund_pending

    # 第二轮：重新发起（同意图同渠道全额 → 视为新尝试，因上一单已终态 failed）
    def accept(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"refund_id": "wx_rf_r3", "status": "PROCESSING"})

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(accept))
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "wxpay", "reason": "服务未履约（重试）"},
        headers=admin,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processing"
    refund_id = resp.json()["id"]

    # 查证恢复：微信查单返回 SUCCESS → 自动结论
    def queried(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"out_refund_no": resp.json()["refund_no"], "refund_id": "wx_rf_r3", "status": "SUCCESS"},
        )

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(queried))
    resp2 = await client.post(f"{API}/admin/mp/refunds/{refund_id}/query", headers=admin)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["status"] == "success"

    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "refunded"
    ents = (await client.get(f"{API}/mp/entitlements", headers=user_headers())).json()["items"]
    assert ents[0]["status"] == "void"


@pytest.mark.asyncio
async def test_r4_timeout_keeps_processing_and_recovers(client, wxpay_env):
    """网络超时＝结果未知：保持 processing 待查证；查证成功后自动入结论。"""
    admin = await admin_headers(client)
    order = await _wxpay_order(client, admin, 5600)
    assert await _notify(client, wxpay_env, _paid_wxpay_order_payload(order["order_no"], 5600, "wx_tx_r4")) == 200

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(timeout))
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "wxpay", "reason": "用户申请"},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    refund = resp.json()
    assert refund["status"] == "processing"  # 不当作失败
    assert "待查证" in (refund["note"] or "")

    # 重复同参数请求：复用同一退款单号（幂等）
    resp2 = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "wxpay", "reason": "用户申请"},
        headers=admin,
    )
    assert resp2.status_code == 200
    assert resp2.json()["refund_no"] == refund["refund_no"]

    # 查证超时 → 504 可重试
    resp3 = await client.post(f"{API}/admin/mp/refunds/{refund['id']}/query", headers=admin)
    assert resp3.status_code == 504

    # 查证成功 → 结论入账
    def recovered(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"out_refund_no": refund["refund_no"], "status": "SUCCESS"}
        )

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(recovered))
    resp4 = await client.post(f"{API}/admin/mp/refunds/{refund['id']}/query", headers=admin)
    assert resp4.status_code == 200
    assert resp4.json()["status"] == "success"
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "refunded"


@pytest.mark.asyncio
async def test_r4_manual_success_resolve_blocked_for_wxpay(client, wxpay_env):
    """线上退款单不得人工直接判成功：必须先查证微信状态。"""
    admin = await admin_headers(client)
    order = await _wxpay_order(client, admin, 4100)
    assert await _notify(client, wxpay_env, _paid_wxpay_order_payload(order["order_no"], 4100, "wx_tx_r4b")) == 200

    def accept(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"refund_id": "wx_rf_r4b", "status": "PROCESSING"})

    wxpay_service.set_http_transport_for_tests(httpx.MockTransport(accept))
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "wxpay", "reason": "服务未履约"},
        headers=admin,
    )
    refund_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/refunds/{refund_id}/resolve",
        json={"status": "success", "note": "人工"},
        headers=admin,
    )
    assert resp.status_code == 409
    assert "查证" in resp.json()["detail"]
    # 失败结论仍可人工登记（有微信明确错误时）
    resp = await client.post(
        f"{API}/admin/mp/refunds/{refund_id}/resolve",
        json={"status": "failed", "note": "微信关闭"},
        headers=admin,
    )
    assert resp.status_code == 200


# ───────────────────────── R5 部分退款与一致性 ─────────────────────────


@pytest.mark.asyncio
async def test_r5_partial_refund_blocked_without_rules(client, wxpay_env):
    """未确认部分退款规则：部分金额请求被明确阻断。"""
    admin = await admin_headers(client)
    order = await _wxpay_order(client, admin, 9900)
    assert await _notify(client, wxpay_env, _paid_wxpay_order_payload(order["order_no"], 9900, "wx_tx_r5")) == 200
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "manual", "amount_cents": 5000, "reason": "想退一半"},
        headers=admin,
    )
    assert resp.status_code == 409
    assert "部分退款" in resp.json()["detail"]
    detail = (await client.get(f"{API}/mp/orders/{order['id']}", headers=user_headers())).json()
    assert detail["status"] == "paid"  # 订单与权益未被部分退款影响


@pytest.mark.asyncio
async def test_r5_full_refund_single_revert_and_events(client):
    """积分单全额退款：积分恰返还一次；重复结论/重复申请被拒；事件留痕。"""
    admin = await admin_headers(client)
    made = await make_package(client, admin, points_cost=600, price_cents=6000)
    resp = await client.post(
        f"{API}/admin/mp/points/adjust",
        json={"user_id": 1, "delta": 600, "note": "前置"},
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
    order = resp.json()

    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "manual", "reason": "用户申请全额退款"},
        headers=admin,
    )
    refund_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/refunds/{refund_id}/resolve",
        json={"status": "success", "note": "线下退款凭据 R5-001"},
        headers=admin,
    )
    assert resp.status_code == 200

    # 积分恰返还一次
    resp = (await client.get(f"{API}/mp/points", headers=user_headers())).json()
    assert resp["balance"] == 600
    entries = (await client.get(f"{API}/mp/points/entries", headers=user_headers())).json()["items"]
    reverts = [e for e in entries if e["biz_type"] == "refund_revert"]
    assert len(reverts) == 1

    # 已退款订单不可再申请
    resp = await client.post(
        f"{API}/admin/mp/orders/{order['id']}/refund",
        json={"channel": "manual", "reason": "再退一次"},
        headers=admin,
    )
    assert resp.status_code == 409

    # 权益事件：issued + voided（含退款单号），工作二可消费
    ents = (await client.get(f"{API}/mp/entitlements", headers=user_headers())).json()["items"]
    ent_id = ents[0]["id"]
    resp = await client.get(f"{API}/mp/entitlements/{ent_id}/events", headers=user_headers())
    events = resp.json()
    types = [e["event_type"] for e in events]
    assert types == ["voided", "issued"]
    assert events[0]["detail"]["refund_no"].startswith("RF")


# ───────────────────────── R6 跨批次防重 ─────────────────────────


async def _preview_and_confirm(client, admin, org_id, made, phone, confirm=True):
    resp = await client.post(
        f"{API}/admin/mp/batches/preview",
        json={
            "org_id": org_id,
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "quantity": 5,
            "file_name": "r6.csv",
            "rows": [phone],
        },
        headers=admin,
    )
    assert resp.status_code == 201
    batch_id = resp.json()["batch"]["id"]
    if not confirm:
        return batch_id
    resp = await client.post(f"{API}/admin/mp/batches/{batch_id}/confirm", headers=admin)
    return batch_id, resp


@pytest.mark.asyncio
async def test_r6_concurrent_batches_single_entitlement(client):
    """两个批次预览同号后先后确认：第二个 409，有效权益至多一份。"""
    admin = await admin_headers(client)
    resp = await client.post(f"{API}/admin/mp/orgs", json={"name": "R6 单位"}, headers=admin)
    org_id = resp.json()["id"]
    made = await make_package(client, admin)

    # 两个批次都先预览（都判定 valid —— 预览时对方尚未发放）
    _, confirm_resp = await _preview_and_confirm(client, admin, org_id, made, "13800000001", confirm=True)
    assert confirm_resp.status_code == 200

    # 第二批同号：预览通过（第一批已确认发放，跨批次查重标 duplicate）
    resp = await client.post(
        f"{API}/admin/mp/batches/preview",
        json={
            "org_id": org_id,
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "quantity": 5,
            "file_name": "r6-2.csv",
            "rows": ["13800000001"],
        },
        headers=admin,
    )
    assert resp.status_code == 201
    assert resp.json()["rows"][0]["status"] == "duplicate"

    # 直接绕过预览（老批次仍处 preview）场景：同单位再开一批且第二批先行确认前
    # 用第三个批次模拟"两批都已预览后才确认"——通过 API 顺序：批 B 预览 → 批 A 确认 → 批 B 确认
    resp = await client.post(
        f"{API}/admin/mp/orgs", json={"name": "R6 单位乙"}, headers=admin
    )
    org2 = resp.json()["id"]
    # 批 B 预览（此时无冲突）
    resp = await client.post(
        f"{API}/admin/mp/batches/preview",
        json={
            "org_id": org2,
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "quantity": 5,
            "file_name": "r6-b.csv",
            "rows": ["13900001234"],
        },
        headers=admin,
    )
    batch_b = resp.json()["batch"]["id"]
    # 同单位乙再预览同号一批 C（批 B 尚未确认 → 预览也判定 valid，因预览查重只看已发放）
    resp = await client.post(
        f"{API}/admin/mp/batches/preview",
        json={
            "org_id": org2,
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "quantity": 5,
            "file_name": "r6-c.csv",
            "rows": ["13900001234"],
        },
        headers=admin,
    )
    batch_c = resp.json()["batch"]["id"]
    assert resp.json()["rows"][0]["status"] == "valid"

    resp = await client.post(f"{API}/admin/mp/batches/{batch_b}/confirm", headers=admin)
    assert resp.status_code == 200
    # 批 C 确认时撞上批 B 已发放：整批 409，不静默少发
    resp = await client.post(f"{API}/admin/mp/batches/{batch_c}/confirm", headers=admin)
    assert resp.status_code == 409
    assert "重复手机号" in resp.json()["detail"]

    # 数据库层面：该单位该版本该号非 void 权益恰一份
    ents = (await client.get(f"{API}/admin/mp/entitlements?q=1234", headers=admin)).json()
    active_1234 = [e for e in ents["items"] if (e.get("phone") or "").endswith("1234")]
    assert len(active_1234) == 1
    # 批 C 状态仍是 preview，可取消
    resp = await client.post(f"{API}/admin/mp/batches/{batch_c}/cancel", headers=admin)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_r6_reassign_hits_guard(client):
    """改号到同单位同版本已有权益的号码：被唯一约束/预检拒绝。"""
    admin = await admin_headers(client)
    resp = await client.post(f"{API}/admin/mp/orgs", json={"name": "R6 改号单位"}, headers=admin)
    org_id = resp.json()["id"]
    made = await make_package(client, admin)
    await _preview_and_confirm(client, admin, org_id, made, "13800000001", confirm=True)
    await _preview_and_confirm(client, admin, org_id, made, "13900005678", confirm=True)

    resp = await client.get(f"{API}/admin/mp/entitlements?q=5678", headers=admin)
    ent = resp.json()["items"][0]
    resp = await client.post(
        f"{API}/admin/mp/entitlements/{ent['id']}/reassign",
        json={"phone": "13800000001", "reason": "错号改派到已占号码"},
        headers=admin,
    )
    assert resp.status_code == 409
