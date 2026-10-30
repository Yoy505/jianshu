"""F05 非终态回调幂等（批次 D1）。

回调先持久化事件（UNIQUE(order_id, event_key)）再按允许的原状态条件更新；尝试次数只在
拨打时计一次。覆盖：串行与并发重复投递只应用一次、旧尝试晚回不覆盖新尝试、取消 / 终态 /
未拨出工单拒绝、格式错误 4xx、签名失败不改状态且不留事件。
"""

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.models import PhoneCallbackEvent, PhoneCallOrder, PhoneCallAttempt
from app.services import phone_service as svc
from tests.commerce_helpers import API
from tests.test_phone_domain import _create_order, _publish_script, _setup_with_phone_consents

pytestmark = pytest.mark.asyncio
SECRET = b"test-ccc-secret"


class AcceptedAdapter:
    """真实 CCC 形态：发起即受理（accepted + request_id），结果由回调收尾。"""

    def __init__(self):
        self.calls = 0

    async def place_call(self, order, script, phone):
        self.calls += 1
        return {"outcome": "accepted", "request_id": f"REQ-{self.calls}", "simulated": False}


@pytest.fixture(autouse=True)
def _adapters(monkeypatch):
    monkeypatch.setattr(settings, "STEWARD_PHONE_CCC_SECRET", SECRET.decode())
    svc.clear_adapters()
    yield
    svc.clear_adapters()


def _signed(payload: dict) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    return body, hmac.new(SECRET, body, hashlib.sha256).hexdigest()


async def _post(client, a, payload: dict, *, signature: str | None = "auto"):
    body, sig = _signed(payload)
    headers = {"Authorization": a["Authorization"], "Content-Type": "application/json"}
    if signature == "auto":
        headers["X-Phone-Signature"] = sig
    elif signature is not None:
        headers["X-Phone-Signature"] = signature
    return await client.post(f"{API}/admin/phone/callback", content=body, headers=headers)


async def _order_row(client, order_id: int) -> PhoneCallOrder:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        return (await session.execute(select(PhoneCallOrder).where(PhoneCallOrder.id == order_id))).scalar_one()


async def _events(client, order_id: int) -> list[str]:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        rows = (await session.execute(
            select(PhoneCallbackEvent).where(PhoneCallbackEvent.order_id == order_id).order_by(PhoneCallbackEvent.id)
        )).scalars().all()
        return [r.result for r in rows]


async def _dispatched_order(client) -> tuple[dict, dict, int]:
    ctx = await _setup_with_phone_consents(client)
    a = ctx["admin"]
    script_id = await _publish_script(client, a)
    order_id = (await _create_order(client, a, ctx, script_id)).json()["id"]
    resp = await client.post(f"{API}/admin/phone/orders/{order_id}/review", json={"approve": True}, headers=a)
    assert resp.status_code == 200, resp.text
    svc.register_adapter("ccc", AcceptedAdapter())
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.dispatch_order(session, order_id)
    assert result["outcome"] == "accepted" and result["attempts"] == 1
    return ctx, a, order_id


async def test_same_no_answer_callback_applies_once_and_never_double_counts(client):
    _ctx, a, order_id = await _dispatched_order(client)
    row = await _order_row(client, order_id)
    assert row.status == "in_progress" and row.attempts == 1 and row.external_request_id == "REQ-1"

    payload = {"order_id": order_id, "outcome": "no_answer", "receipt_ref": "CCC-NA-1",
               "event_id": "EVT-1", "attempt_no": 1, "request_id": "REQ-1"}
    resp = await _post(client, a, payload)
    assert resp.status_code == 200 and resp.json()["applied"] is True and resp.json()["result"] == "applied"
    row = await _order_row(client, order_id)
    assert row.status == "queued" and row.attempts == 1 and row.receipt_ref == "CCC-NA-1"  # 尝试不重复计数

    for _ in range(2):  # 串行重放两次
        resp = await _post(client, a, payload)
        assert resp.status_code == 200 and resp.json()["applied"] is False and resp.json()["result"] == "duplicate"
    row = await _order_row(client, order_id)
    assert row.status == "queued" and row.attempts == 1
    assert await _events(client, order_id) == ["applied"]  # 重放只落审计，不再新增事件行（UNIQUE）


async def test_stale_attempt_callback_does_not_override_newer_attempt(client):
    _ctx, a, order_id = await _dispatched_order(client)
    resp = await _post(client, a, {"order_id": order_id, "outcome": "no_answer", "event_id": "E1",
                                   "attempt_no": 1, "request_id": "REQ-1"})
    assert resp.json()["applied"] is True
    # 到重试时间，再次拨打 → 第 2 次尝试（attempts=2，REQ-2）
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        await session.execute(update(PhoneCallOrder).where(PhoneCallOrder.id == order_id)
                              .values(next_retry_at=svc.now_utc() - timedelta(minutes=1)))
        await session.commit()
        result = await svc.dispatch_order(session, order_id)
    assert result["attempts"] == 2 and result["status"] == "in_progress"

    # 审核反例：缺少全部关联标识不能默认命中当前轮次。
    resp = await _post(client, a, {"order_id": order_id, "outcome": "completed", "event_id": "no-correlation"})
    assert resp.status_code == 422
    assert (await _order_row(client, order_id)).status == "in_progress"

    # 第 1 次尝试的 completed 晚回：attempt_no / request_id 都对不上 → stale，不覆盖
    resp = await _post(client, a, {"order_id": order_id, "outcome": "completed", "event_id": "E-late",
                                   "attempt_no": 1, "request_id": "REQ-1"})
    assert resp.status_code == 200 and resp.json()["applied"] is False and resp.json()["result"] == "stale"
    row = await _order_row(client, order_id)
    assert row.status == "in_progress" and row.attempts == 2
    # 只带 request_id 不符同样 stale
    resp = await _post(client, a, {"order_id": order_id, "outcome": "busy", "event_id": "E-req", "request_id": "REQ-1"})
    assert resp.json()["result"] == "stale"
    # 当前尝试的 completed → 应用
    resp = await _post(client, a, {"order_id": order_id, "outcome": "completed", "event_id": "E2",
                                   "attempt_no": 2, "request_id": "REQ-2", "receipt_ref": "CCC-OK"})
    assert resp.json()["applied"] is True
    row = await _order_row(client, order_id)
    assert row.status == "completed" and row.attempts == 2 and row.receipt_ref == "CCC-OK"
    assert await _events(client, order_id) == ["applied", "stale", "stale", "applied"]


async def test_request_id_requires_matching_persisted_attempt_and_one_result(client):
    _ctx, a, order_id = await _dispatched_order(client)
    async with client.session_factory() as session:
        await session.execute(update(PhoneCallOrder).where(PhoneCallOrder.id == order_id).values(external_request_id=None))
        await session.commit()
    resp = await _post(client, a, {"order_id": order_id, "outcome": "completed", "request_id": "unknown"})
    assert resp.json()["applied"] is False and resp.json()["result"] == "stale"
    # attempt_no 可以独立关联，但第二个结果事件不能覆盖第一次结果。
    resp = await _post(client, a, {"order_id": order_id, "outcome": "no_answer", "attempt_no": 1, "event_id": "first"})
    assert resp.json()["applied"] is True
    resp = await _post(client, a, {"order_id": order_id, "outcome": "completed", "attempt_no": 1, "event_id": "second"})
    assert resp.json()["applied"] is False
    assert (await _order_row(client, order_id)).status == "queued"


async def test_retry_is_persisted_before_external_call_and_rejects_old_request(client):
    _ctx, a, order_id = await _dispatched_order(client)
    assert (await _post(client, a, {"order_id": order_id, "outcome": "no_answer", "attempt_no": 1})).json()["applied"]

    class CheckBeforeAccept:
        async def place_call(self, order, script, phone):
            assert order.attempts == 2
            async with client.session_factory() as db:
                attempt = (await db.execute(select(PhoneCallAttempt).where(
                    PhoneCallAttempt.order_id == order.id, PhoneCallAttempt.attempt_no == 2,
                ))).scalar_one()
                assert attempt.finished_at is None
            # 第二轮供应商尚未返回请求号，也不能用上一轮请求号套用到当前工单。
            resp = await _post(client, a, {"order_id": order.id, "outcome": "completed", "request_id": "REQ-1"})
            assert resp.json()["result"] == "stale" and not resp.json()["applied"]
            return {"outcome": "accepted", "request_id": "REQ-2", "simulated": False}

    svc.register_adapter("ccc", CheckBeforeAccept())
    async with client.session_factory() as db:
        await db.execute(update(PhoneCallOrder).where(PhoneCallOrder.id == order_id).values(
            next_retry_at=svc.now_utc() - timedelta(minutes=1)))
        await db.commit()
        result = await svc.dispatch_order(db, order_id)
    assert result["status"] == "in_progress" and result["attempts"] == 2


async def test_cancelled_terminal_and_never_dialed_orders_reject_callbacks(client):
    ctx, a, order_id = await _dispatched_order(client)
    resp = await client.post(f"{API}/admin/phone/orders/{order_id}/cancel", json={"reason": "用户撤回"}, headers=a)
    assert resp.status_code == 200
    resp = await _post(client, a, {"order_id": order_id, "outcome": "no_answer", "event_id": "after-cancel", "attempt_no": 1})
    assert resp.status_code == 200 and resp.json()["applied"] is False and resp.json()["result"] == "rejected_state"
    row = await _order_row(client, order_id)
    assert row.status == "cancelled" and row.attempts == 1  # 取消后不恢复拨打、不计尝试

    # 从未拨出（审核中）的工单：回调同样拒绝
    script_id = await _publish_script(client, a, code="never_dialed")
    fresh_id = (await _create_order(client, a, ctx, script_id)).json()["id"]
    resp = await _post(client, a, {"order_id": fresh_id, "outcome": "completed", "event_id": "pre-dial", "attempt_no": 1})
    assert resp.json()["result"] == "stale"
    row = await _order_row(client, fresh_id)
    assert row.status == "pending_review" and row.attempts == 0


async def test_format_errors_4xx_and_bad_signature_changes_nothing(client):
    _ctx, a, order_id = await _dispatched_order(client)
    headers = {"Authorization": a["Authorization"], "Content-Type": "application/json"}
    bad = b"{not json"
    sig = hmac.new(SECRET, bad, hashlib.sha256).hexdigest()
    resp = await client.post(f"{API}/admin/phone/callback", content=bad, headers={**headers, "X-Phone-Signature": sig})
    assert resp.status_code == 422
    assert (await _post(client, a, {"order_id": order_id, "outcome": "teleported"})).status_code == 422
    assert (await _post(client, a, {"order_id": "abc", "outcome": "completed"})).status_code == 422
    assert (await _post(client, a, {"order_id": order_id, "outcome": "completed", "attempt_no": "x"})).status_code == 422
    assert (await _post(client, a, {"order_id": 999_999, "outcome": "completed"})).status_code == 404
    # 签名错误 / 缺失：403，状态不变，不留事件
    assert (await _post(client, a, {"order_id": order_id, "outcome": "completed"}, signature="bad")).status_code == 403
    assert (await _post(client, a, {"order_id": order_id, "outcome": "completed"}, signature=None)).status_code == 403
    row = await _order_row(client, order_id)
    assert row.status == "in_progress" and row.attempts == 1
    assert await _events(client, order_id) == []


async def test_concurrent_duplicate_delivery_applies_exactly_once(client, tmp_path, monkeypatch):
    """三个真实并发连接（SQLite 文件库或所选 PostgreSQL 夹具）投递同一事件：UNIQUE 保证恰一次应用。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db.base import Base

    engine = None
    if client.database_backend == "postgresql":
        _ctx, _a, order_id = await _dispatched_order(client)
        factory = client.session_factory
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cb.db'}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            session.add(PhoneCallOrder(
                instance_id=1, user_id=1, purpose="followup_reminder", target_type="self", script_id=1,
                script_code="followup_reminder", script_version="v1", status="in_progress", attempts=1,
                external_request_id="REQ-1",
            ))
            await session.commit()
            order_id = (await session.execute(select(PhoneCallOrder.id))).scalar_one()

        async with factory() as session:
            session.add(PhoneCallAttempt(order_id=order_id, attempt_no=1, outcome="accepted",
                                         external_request_id="REQ-1", started_at=svc.now_utc()))
            await session.commit()

    body, sig = _signed({"order_id": order_id, "outcome": "no_answer", "event_id": "EVT-C", "attempt_no": 1})

    async def deliver():
        async with factory() as session:
            return await svc.handle_callback(session, body, sig)

    results = await asyncio.gather(deliver(), deliver(), deliver())
    applied = [r for r in results if r["applied"]]
    assert len(applied) == 1 and all(r["result"] == "duplicate" for r in results if not r["applied"])
    async with factory() as session:
        row = (await session.execute(select(PhoneCallOrder).where(PhoneCallOrder.id == order_id))).scalar_one()
        assert row.status == "queued" and row.attempts == 1
        events = (await session.execute(select(PhoneCallbackEvent))).scalars().all()
        assert len(events) == 1 and events[0].result == "applied"
    if engine is not None:
        await engine.dispose()
