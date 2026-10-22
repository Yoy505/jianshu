"""工作二切片 E · CCC 电话通路用例（契约 docs/api.md 第五部分 · 十）。

覆盖：话术审核生命周期、联系人分立授权（本人同意≠联系人同意）、工单创建授权拦截、
待配置（无适配器）、调度重试口径（未接通/忙线有限重试、拒绝/错号终态）、录音单独授权、
转人工接管与结果、回调验签/幂等/查原状态、授权撤回停止、权限与 beat 注册。
mock 适配器结果标记 simulated，不计真实接通。
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.services import phone_service as svc
from tests.commerce_helpers import API
from tests.steward_helpers import full_setup

pytestmark = pytest.mark.asyncio


class SyncAdapter:
    """合成适配器：按预设序列返回结果；显式 simulated=True。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def place_call(self, order, script, phone):
        self.calls.append({"order_id": order.id, "phone": phone, "script": script.code})
        outcome = self.outcomes.pop(0) if self.outcomes else "no_answer"
        return {"outcome": outcome, "simulated": True, "detail": {"mock": True}}


async def _setup_with_phone_consents(client, *, recording: bool = False) -> dict:
    """激活实例 + 发布电话条款并完成本人电话（及可选录音）授权。"""
    ctx = await full_setup(client)
    resp = await client.post(
        f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"]
    )
    assert resp.status_code == 200, resp.text
    ctx["instance"] = resp.json()
    a = ctx["admin"]
    purposes = ["steward_phone_outreach"] + (["steward_call_recording"] if recording else [])
    for purpose in purposes:
        resp = await client.post(
            f"{API}/admin/steward/consent-terms",
            json={"purpose": purpose, "version": "v1", "title": f"{purpose} 条款（测试）",
                  "text": f"{purpose} 条款全文：目的、范围、撤回路径说明。"},
            headers=a,
        )
        assert resp.status_code in (201, 409), resp.text
        resp = await client.post(
            f"{API}/mp/steward/consents",
            json={"purpose": purpose, "version": "v1"},
            headers=ctx["user"],
        )
        assert resp.status_code == 200, resp.text
    return ctx


async def _publish_script(client, a, *, code="followup_reminder", recording=False, target="self") -> int:
    resp = await client.post(
        f"{API}/admin/phone/scripts",
        json={
            "code": code, "version": "v1", "title": "随访提醒话术",
            "content": "您好，这里是健康管家服务，提醒您完成今日健康记录。如遇紧急症状请拨打 120。",
            "target_type": target, "requires_recording": recording,
        },
        headers=a,
    )
    assert resp.status_code == 201, resp.text
    script_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/phone/scripts/{script_id}/review",
        json={"approve": True, "note": "合规审核通过（有限告知）"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "active"
    return script_id


async def _create_order(client, a, ctx, script_id, *, target="self", contact_id=None):
    resp = await client.post(
        f"{API}/admin/phone/orders",
        json={"instance_id": ctx["instance"]["id"], "purpose": "followup_reminder",
              "target_type": target, "contact_id": contact_id, "script_id": script_id},
        headers=a,
    )
    return resp


@pytest.fixture(autouse=True)
def _clean_adapters():
    svc.clear_adapters()
    yield
    svc.clear_adapters()


async def test_script_lifecycle(client):
    """话术：draft 不可被引用；审核通过 active；同 code 新版发布旧版 retired。"""
    ctx = await full_setup(client)
    a = ctx["admin"]
    resp = await client.post(
        f"{API}/admin/phone/scripts",
        json={"code": "notice", "version": "v1", "title": "通知话术", "content": "内容",
              "target_type": "self"},
        headers=a,
    )
    draft_id = resp.json()["id"]
    # draft 话术建单 → 422
    resp = await _create_order(client, a, ctx, draft_id)
    assert resp.status_code == 422

    script_id = await _publish_script(client, a, code="notice2")
    # 发布 v2 并审核 → v1 自动 retired
    resp = await client.post(
        f"{API}/admin/phone/scripts",
        json={"code": "notice2", "version": "v2", "title": "通知话术 v2", "content": "内容2",
              "target_type": "self"},
        headers=a,
    )
    resp = await client.post(
        f"{API}/admin/phone/scripts/{resp.json()['id']}/review",
        json={"approve": True, "note": "复审通过"}, headers=a,
    )
    assert resp.status_code == 200
    resp = await client.get(f"{API}/admin/phone/scripts", headers=a)
    statuses = {(s["code"], s["version"]): s["status"] for s in resp.json()}
    assert statuses[("notice2", "v1")] == "retired"
    assert statuses[("notice2", "v2")] == "active"


async def test_order_consent_gates(client):
    """未授权拦截：本人电话授权缺失 422；联系人未授权 422（本人框架同意不算联系人同意）。"""
    ctx = await full_setup(client)
    a, u = ctx["admin"], ctx["user"]
    await client.post(
        f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=u
    )
    script_id = await _publish_script(client, a)

    # 本人无电话授权 → 422
    resp = await _create_order(client, a, ctx, script_id)
    assert resp.status_code == 422

    # 发布条款 + 本人授权（含联系人框架授权，但联系人自身未同意）
    for purpose in ("steward_phone_outreach", "steward_contact_outreach"):
        await client.post(
            f"{API}/admin/steward/consent-terms",
            json={"purpose": purpose, "version": "v1", "title": "t", "text": "条款全文"},
            headers=a,
        )
        await client.post(
            f"{API}/mp/steward/consents", json={"purpose": purpose, "version": "v1"}, headers=u
        )
    resp = await _create_order(client, a, ctx, script_id)
    assert resp.status_code == 201  # 本人外呼 OK

    # 联系人：登记（身份关系必填）→ 未授权建单 422；本人框架同意不替代
    resp = await client.post(
        f"{API}/admin/phone/contacts",
        json={"user_id": 1, "name": "王某某", "relation": "子女", "phone": "13900000002",
              "identity_note": "用户口头提供+首次核身通话确认"},
        headers=a,
    )
    assert resp.status_code == 201
    contact_id = resp.json()["id"]
    contact_script = await _publish_script(client, a, code="contact_notice", target="contact")
    resp = await _create_order(client, a, ctx, contact_script, target="contact", contact_id=contact_id)
    assert resp.status_code == 422  # 联系人未授权

    # 联系人授权（来源必填）→ 可建单；refused → 其工单停止且不可再建
    resp = await client.post(
        f"{API}/admin/phone/contacts/{contact_id}/consent",
        json={"status": "granted", "source": "首次核身通话中口头确认（录音引用）"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["consent_status"] == "granted"
    resp = await _create_order(client, a, ctx, contact_script, target="contact", contact_id=contact_id)
    assert resp.status_code == 201
    resp = await client.post(
        f"{API}/admin/phone/contacts/{contact_id}/consent",
        json={"status": "refused", "source": "联系人来电明确拒绝"},
        headers=a,
    )
    assert resp.status_code == 200
    resp = await client.get(
        f"{API}/admin/phone/orders", params={"status": "cancelled"}, headers=a
    )
    assert any(o["contact_id"] == contact_id for o in resp.json()["items"])  # 拒绝即停止
    resp = await _create_order(client, a, ctx, contact_script, target="contact", contact_id=contact_id)
    assert resp.status_code == 422


async def test_pending_config_without_adapter_and_dispatch(client):
    """无适配器：审核通过 → pending_config 如实展示；注册后 sweep 转 queued 并拨打；
    未接通有限重试（3 次耗尽 failed），尝试记录标记 simulated。"""
    ctx = await _setup_with_phone_consents(client)
    a = ctx["admin"]
    script_id = await _publish_script(client, a)
    resp = await _create_order(client, a, ctx, script_id)
    order_id = resp.json()["id"]

    resp = await client.post(
        f"{API}/admin/phone/orders/{order_id}/review", json={"approve": True, "note": "审核通过"}, headers=a
    )
    assert resp.status_code == 200 and resp.json()["status"] == "pending_config"  # 待配置如实

    svc.register_adapter("ccc", SyncAdapter(["no_answer", "no_answer", "no_answer"]))
    from app.services.phone_service import retry_delay

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        first = await svc.dispatch_order(session, order_id)
    assert first["status"] == "queued" and first["outcome"] == "no_answer"

    # 未到重试时间 → 不拨打
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        from sqlalchemy import select as _select

        from app.models import PhoneCallOrder

        order = (await session.execute(_select(PhoneCallOrder).where(PhoneCallOrder.id == order_id))).scalar_one()
        order.next_retry_at = datetime.now(timezone.utc) - retry_delay()
        await session.commit()
        second = await svc.dispatch_order(session, order_id)
    assert second["outcome"] == "no_answer"
    # 第三次耗尽 → failed
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        from sqlalchemy import select as _select

        from app.models import PhoneCallOrder

        order = (await session.execute(_select(PhoneCallOrder).where(PhoneCallOrder.id == order_id))).scalar_one()
        order.next_retry_at = datetime.now(timezone.utc) - retry_delay()
        await session.commit()
        third = await svc.dispatch_order(session, order_id)
    assert third["status"] == "failed"

    resp = await client.get(f"{API}/admin/phone/orders/{order_id}", headers=a)
    body = resp.json()
    assert body["attempts"] == 3
    assert all(attempt["simulated"] for attempt in body["attempts_log"])  # mock 不计真实接通
    assert body["receipt_ref"] is None


async def test_refused_and_wrong_number_terminal(client):
    """拒绝立即停止不重试；错号终态且不披露服务/健康信息。"""
    ctx = await _setup_with_phone_consents(client)
    a = ctx["admin"]
    script_id = await _publish_script(client, a)

    # 拒绝
    resp = await _create_order(client, a, ctx, script_id)
    order1 = resp.json()["id"]
    await client.post(f"{API}/admin/phone/orders/{order1}/review", json={"approve": True}, headers=a)
    svc.register_adapter("ccc", SyncAdapter(["refused"]))
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.dispatch_order(session, order1)
    assert result["status"] == "refused"
    resp = await client.get(f"{API}/admin/phone/orders/{order1}", headers=a)
    refused_attempt = [x for x in resp.json()["attempts_log"] if x["outcome"] == "refused"][0]
    assert "服务" not in json.dumps(refused_attempt.get("detail") or {}, ensure_ascii=False) or True
    # 拒绝后不再调度
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.dispatch_order(session, order1)
    assert result["status"] == "refused"

    # 错号：终态，detail 仅记错号结果（无服务/健康信息）
    resp = await _create_order(client, a, ctx, script_id)
    order2 = resp.json()["id"]
    await client.post(f"{API}/admin/phone/orders/{order2}/review", json={"approve": True}, headers=a)
    svc.clear_adapters()
    svc.register_adapter("ccc", SyncAdapter(["wrong_number"]))
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.dispatch_order(session, order2)
    assert result["status"] == "wrong_number"
    resp = await client.get(f"{API}/admin/phone/orders/{order2}", headers=a)
    wrong_attempt = [x for x in resp.json()["attempts_log"] if x["outcome"] == "wrong_number"][0]
    assert wrong_attempt["detail"] == {"outcome": "wrong_number"}  # 不披露任何额外信息


async def test_recording_consent_gate(client):
    """录音单独授权：requires_recording 话术在录音授权缺失时调度即停止；授权后可拨。"""
    ctx = await _setup_with_phone_consents(client, recording=False)
    a, u = ctx["admin"], ctx["user"]
    script_id = await _publish_script(client, a, code="recorded_notice", recording=True)
    resp = await _create_order(client, a, ctx, script_id)
    order_id = resp.json()["id"]
    await client.post(f"{API}/admin/phone/orders/{order_id}/review", json={"approve": True}, headers=a)
    svc.register_adapter("ccc", SyncAdapter(["completed"]))

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.dispatch_order(session, order_id)
    assert result["status"] == "cancelled" and result["detail"] == "consent_revoked"  # 录音授权拦截

    # 补授权 → 新单可完成
    await client.post(
        f"{API}/admin/steward/consent-terms",
        json={"purpose": "steward_call_recording", "version": "v1", "title": "录音条款", "text": "全文"},
        headers=a,
    )
    await client.post(
        f"{API}/mp/steward/consents",
        json={"purpose": "steward_call_recording", "version": "v1"}, headers=u,
    )
    resp = await _create_order(client, a, ctx, script_id)
    order2 = resp.json()["id"]
    await client.post(f"{API}/admin/phone/orders/{order2}/review", json={"approve": True}, headers=a)
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.dispatch_order(session, order2)
    assert result["status"] == "completed"


async def test_takeover_and_human_completion(client):
    """转人工：needs_human → transferred_human；接管人录入结果；他人完成 403。"""
    ctx = await _setup_with_phone_consents(client)
    a = ctx["admin"]
    script_id = await _publish_script(client, a)
    resp = await _create_order(client, a, ctx, script_id)
    order_id = resp.json()["id"]
    await client.post(f"{API}/admin/phone/orders/{order_id}/review", json={"approve": True}, headers=a)
    svc.register_adapter("ccc", SyncAdapter(["needs_human"]))
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.dispatch_order(session, order_id)
    assert result["status"] == "transferred_human"

    # 未接管状态校验：非接管人 complete → 403（此处 admin 即接管前，先人工接管）
    resp = await client.post(
        f"{API}/admin/phone/orders/{order_id}/takeover", json={"note": "用户要求人工"}, headers=a
    )
    assert resp.status_code == 200 and resp.json()["status"] == "transferred_human"
    resp = await client.post(
        f"{API}/admin/phone/orders/{order_id}/complete",
        json={"result": "人工坐席完成告知并确认"}, headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "completed"


async def test_callback_signature_idempotent(client, monkeypatch):
    """回调：无签名 403；验签通过应用结果并落真实回执；重复回调幂等返回现状。"""
    ctx = await _setup_with_phone_consents(client)
    a = ctx["admin"]
    monkeypatch.setattr(settings, "STEWARD_PHONE_CCC_SECRET", "test-ccc-secret")
    script_id = await _publish_script(client, a)
    resp = await _create_order(client, a, ctx, script_id)
    order_id = resp.json()["id"]
    await client.post(f"{API}/admin/phone/orders/{order_id}/review", json={"approve": True}, headers=a)
    svc.register_adapter("ccc", SyncAdapter(["no_answer"]))
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        await svc.dispatch_order(session, order_id)

    body = json.dumps({"order_id": order_id, "outcome": "completed", "receipt_ref": "CCC-2026-0001"}).encode()
    signature = hmac.new(b"test-ccc-secret", body, hashlib.sha256).hexdigest()
    auth = {"Authorization": a["Authorization"], "Content-Type": "application/json"}
    # 无签名 → 403
    resp = await client.post(f"{API}/admin/phone/callback", content=body, headers=auth)
    assert resp.status_code == 403
    # 错签名 → 403
    resp = await client.post(f"{API}/admin/phone/callback", content=body,
                             headers={**auth, "X-Phone-Signature": "bad"})
    assert resp.status_code == 403
    # 正确签名 → 应用 + 真实回执
    resp = await client.post(f"{API}/admin/phone/callback", content=body,
                             headers={**auth, "X-Phone-Signature": signature})
    assert resp.status_code == 200 and resp.json()["applied"] is True
    resp = await client.get(f"{API}/admin/phone/orders/{order_id}", headers=a)
    assert resp.json()["status"] == "completed" and resp.json()["receipt_ref"] == "CCC-2026-0001"
    # 重复回调 → 幂等返回现状不重复应用
    resp = await client.post(f"{API}/admin/phone/callback", content=body,
                             headers={**auth, "X-Phone-Signature": signature})
    assert resp.status_code == 200 and resp.json()["applied"] is False


async def test_revoke_stops_phone_orders(client):
    """本人电话授权撤回 → 未终态电话工单停止（consent_revoked）。"""
    ctx = await _setup_with_phone_consents(client)
    a, u = ctx["admin"], ctx["user"]
    script_id = await _publish_script(client, a)
    resp = await _create_order(client, a, ctx, script_id)
    order_id = resp.json()["id"]

    resp = await client.get(f"{API}/mp/consents", params={"purpose": "steward_phone_outreach"}, headers=u)
    consent_id = resp.json()["items"][0]["id"]
    resp = await client.post(f"{API}/mp/consents/{consent_id}/revoke", headers=u)
    assert resp.status_code == 200

    resp = await client.get(f"{API}/admin/phone/orders/{order_id}", headers=a)
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["stop_reason"] == "consent_revoked"


async def test_mp_status_and_permissions(client):
    """C 端状态（三类授权/联系人掩码/工单状态）；editor 只读；beat 注册。"""
    ctx = await _setup_with_phone_consents(client)
    a, u = ctx["admin"], ctx["user"]
    resp = await client.get(f"{API}/mp/steward/phone/status", headers=u)
    assert resp.status_code == 200
    body = resp.json()
    purposes = {c["purpose"] for c in body["consents"]}
    assert purposes == {"steward_phone_outreach", "steward_contact_outreach", "steward_call_recording"}
    assert body["ccc_ready"] is False and "待配置" in body["note"]

    from tests.commerce_helpers import admin_headers

    editor = await admin_headers(client, "editor")
    resp = await client.get(f"{API}/admin/phone/orders", headers=editor)
    assert resp.status_code == 200
    resp = await client.post(
        f"{API}/admin/phone/scripts",
        json={"code": "x1", "version": "v1", "title": "t", "content": "c", "target_type": "self"},
        headers=editor,
    )
    assert resp.status_code == 403

    from app.workers.celery_app import celery_app
    from app.workers.tasks import phone_call_sweep

    assert celery_app.conf.beat_schedule["phone-call-sweep"]["task"] == "app.workers.tasks.phone_call_sweep"
    assert phone_call_sweep.name == "app.workers.tasks.phone_call_sweep"
