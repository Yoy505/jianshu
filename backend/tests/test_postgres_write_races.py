"""真实 PostgreSQL 写入竞争；必须显式 --business-db=postgres，不以 SQLite 冒充行锁证据。"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.sql.dml import Update

from app.core.config import settings
from app.models import (
    PhoneCallbackEvent, PhoneCallAttempt, PhoneCallOrder, StewardCarePlan,
    StewardServiceInstance, StewardServiceReport, StewardStaff, StewardTask,
)
from app.services import phone_service as phone, professional_service as prof
from tests.commerce_helpers import API
from tests.steward_helpers import create_staff
from tests.test_physician_identity_chain import _activate, _admin_id, _bind
from tests.test_review_repairs import _review_body, _review_task
from tests.test_phone_callback_idempotency import SECRET, _signed
from tests.test_phone_domain import _create_order, _publish_script, _setup_with_phone_consents

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def require_postgres(request, monkeypatch):
    if request.config.getoption("--business-db") != "postgres":
        pytest.skip("需要 --business-db=postgres 和 JIANSHU_PG_TEST_URL；不使用 SQLite 替代行锁")
    monkeypatch.setattr(settings, "STEWARD_PHONE_CCC_SECRET", SECRET.decode())
    phone.clear_adapters()
    yield
    phone.clear_adapters()


async def _pid(db):
    assert db.get_bind().dialect.name == "postgresql"
    return (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()


async def _wait_for_lock(client, pid, *, completed=None):
    """观察真实数据库阻塞，失败有界；不以固定延时猜测事务交错。"""
    async def wait():
        async with client.session_factory() as db:
            assert await _pid(db) != pid
            while True:
                if completed is not None and completed.done():
                    return False
                if (await db.execute(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                                     {"pid": pid})).scalar_one():
                    return True
                await asyncio.sleep(0.01)
    return await asyncio.wait_for(wait(), 8)


async def _closed_context(client):
    ctx = await _activate(client)
    iid, pid = ctx["instance"]["id"], ctx["staff"]["physician"]["id"]
    await _bind(client, ctx["admin"], pid, await _admin_id(client))
    async with client.session_factory() as db:
        await db.execute(update(StewardServiceInstance).where(StewardServiceInstance.id == iid).values(status="closed"))
        db.add(StewardServiceReport(instance_id=iid, user_id=1, version=1, report_type="closure",
                                   title="原报告", content="原报告", physician_id=pid))
        await db.commit()
    return ctx


@pytest.mark.parametrize("target", ["plan", "external_plan", "verify", "review", "correction"])
@pytest.mark.parametrize("change", ["revoked", "expired", "inactive"])
async def test_credential_change_commits_before_waiting_write(client, monkeypatch, target, change):
    ctx = await _closed_context(client) if target == "correction" else await _activate(client)
    a, iid, pid = ctx["admin"], ctx["instance"]["id"], ctx["staff"]["physician"]["id"]
    if target not in ("correction", "external_plan", "verify"):
        await _bind(client, a, pid, await _admin_id(client))
    body = {"user_id": 1, "physician_id": pid, "org_name": "杏临汇签约服务机构", "version": "v2", "content": {}}
    evidence = {"evidence_ref": "private://verified.pdf", "evidence_source": "机构签发件"}
    plan_id = None
    if target == "verify":
        response = await client.post(f"{API}/admin/steward/care-plans", headers=a, json=body)
        assert response.status_code == 201
        plan_id = response.json()["id"]
    real_guard = prof.guard_physician_authorization

    async def race(db, physician_id, admin_id=None):
        writer_pid = await _pid(db)
        now = datetime.now(timezone.utc)
        changes = {"revoked": {"credential_revoked_at": now},
                   "expired": {"credential_valid_until": now - timedelta(seconds=1)},
                   "inactive": {"status": "inactive"}}[change]
        async with client.session_factory() as revoke:
            await revoke.execute(update(StewardStaff).where(StewardStaff.id == physician_id).values(**changes))
            waiter = asyncio.create_task(real_guard(db, physician_id, admin_id))
            try:
                assert await _wait_for_lock(client, writer_pid)
                await revoke.commit()
                return await asyncio.wait_for(waiter, 8)
            finally:
                if not waiter.done():
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)

    monkeypatch.setattr(prof, "guard_physician_authorization", race)
    if target in ("plan", "external_plan"):
        if target == "external_plan":
            body["external_evidence"] = evidence
        response = await client.post(f"{API}/admin/steward/care-plans", headers=a, json=body)
    elif target == "verify":
        response = await client.post(f"{API}/admin/steward/care-plans/{plan_id}/verify", headers=a, json=evidence)
    elif target == "review":
        task = await _review_task(client, ctx)
        response = await client.post(f"{API}/admin/steward/tasks/{task.id}/result", headers=a, json=_review_body())
    else:
        response = await client.post(f"{API}/admin/steward/reports", headers=a,
                                     json={"instance_id": iid, "title": "更正", "content": "更正"})
    assert response.status_code == 409, response.text
    async with client.session_factory() as db:
        if target == "verify":
            assert (await db.get(StewardCarePlan, plan_id)).issue_source == "ops_entry"
        elif target in ("plan", "external_plan"):
            assert not (await db.execute(select(StewardCarePlan).where(StewardCarePlan.version == "v2"))).scalars().all()
        elif target == "review":
            assert (await db.get(StewardTask, task.id)).status == "pending"
        else:
            assert len((await db.execute(select(StewardServiceReport))).scalars().all()) == 1


async def test_correction_rejects_reassignment_after_identity_check(client, monkeypatch):
    ctx = await _closed_context(client)
    iid, a = ctx["instance"]["id"], ctx["admin"]
    new = await create_staff(client, a, "physician", "接收医师")
    real_check = prof.require_physician_identity

    async def change_after_check(db, admin, physician_id, *, action):
        physician = await real_check(db, admin, physician_id, action=action)
        changed = await client.post(f"{API}/admin/steward/instances/{iid}/assign", headers=a,
                                    json={"physician_id": new["id"]})
        assert changed.status_code == 200
        return physician

    monkeypatch.setattr(prof, "require_physician_identity", change_after_check)
    response = await client.post(f"{API}/admin/steward/reports", headers=a,
                                 json={"instance_id": iid, "title": "更正", "content": "更正"})
    assert response.status_code == 409
    async with client.session_factory() as db:
        assert len((await db.execute(select(StewardServiceReport))).scalars().all()) == 1


async def test_correction_rejects_reassignment_committing_during_row_wait(client, monkeypatch):
    ctx = await _closed_context(client)
    iid, a = ctx["instance"]["id"], ctx["admin"]
    new = await create_staff(client, a, "physician", "接收医师")
    entered, state = asyncio.Event(), {}
    real_check = prof.require_physician_identity

    async def record_pid(db, admin, physician_id, *, action):
        physician = await real_check(db, admin, physician_id, action=action)
        state["pid"] = await _pid(db)
        entered.set()
        return physician

    monkeypatch.setattr(prof, "require_physician_identity", record_pid)
    async with client.session_factory() as changer:
        await changer.execute(update(StewardServiceInstance).where(StewardServiceInstance.id == iid).values(physician_id=new["id"]))
        task = asyncio.create_task(client.post(f"{API}/admin/steward/reports", headers=a,
            json={"instance_id": iid, "title": "更正", "content": "更正"}))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            assert await _wait_for_lock(client, state["pid"])
            await changer.commit()
            response = await asyncio.wait_for(task, 8)
            assert response.status_code == 409
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_credential_can_expire_while_guard_waits(client, monkeypatch):
    ctx = await _activate(client)
    pid, a = ctx["staff"]["physician"]["id"], ctx["admin"]
    aid = await _admin_id(client)
    await _bind(client, a, pid, aid)
    before = datetime.now(timezone.utc)
    monkeypatch.setattr(prof, "now_utc", lambda: before)
    entered, state = asyncio.Event(), {}
    async with client.session_factory() as holder:
        await holder.execute(update(StewardStaff).where(StewardStaff.id == pid).values(
            credential_valid_until=before + timedelta(minutes=1)))
        # 有效期先提交，再单独持行锁，守卫的语句参数在等待前仍判断为有效。
        await holder.commit()
        await holder.execute(update(StewardStaff).where(StewardStaff.id == pid).values(status=StewardStaff.status))
        async def guard():
            async with client.session_factory() as db:
                state["pid"] = await _pid(db)
                entered.set()
                with pytest.raises(HTTPException) as caught:
                    await prof.guard_physician_authorization(db, pid, aid)
                assert getattr(caught.value, "status_code", None) == 409
        waiter = asyncio.create_task(guard())
        try:
            await asyncio.wait_for(entered.wait(), 8)
            assert await _wait_for_lock(client, state["pid"])
            monkeypatch.setattr(prof, "now_utc", lambda: before + timedelta(minutes=2))
            await holder.commit()
            await asyncio.wait_for(waiter, 8)
        finally:
            if not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)


async def test_signing_first_keeps_reassignment_waiting_until_commit(client, monkeypatch):
    ctx = await _closed_context(client)
    iid, a = ctx["instance"]["id"], ctx["admin"]
    old = ctx["staff"]["physician"]["id"]
    new = await create_staff(client, a, "physician", "后续接收医师")
    from app.services import steward_service
    real_audit, real_assign = prof.audit, steward_service.assign_staff
    ready, state, assign_task = asyncio.Event(), {}, None

    async def record_assign_pid(db, admin, instance, payload):
        state["pid"] = await _pid(db)
        ready.set()
        return await real_assign(db, admin, instance, payload)

    async def race_audit(db, **kwargs):
        nonlocal assign_task
        if kwargs.get("action") == "steward.report.correct":
            assign_task = asyncio.create_task(client.post(f"{API}/admin/steward/instances/{iid}/assign", headers=a,
                                                         json={"physician_id": new["id"]}))
            await asyncio.wait_for(ready.wait(), 8)
            assert await _wait_for_lock(client, state["pid"])
        return await real_audit(db, **kwargs)

    monkeypatch.setattr(steward_service, "assign_staff", record_assign_pid)
    monkeypatch.setattr(prof, "audit", race_audit)
    try:
        response = await client.post(f"{API}/admin/steward/reports", headers=a,
                                     json={"instance_id": iid, "title": "更正", "content": "先签发"})
        assert response.status_code == 201 and response.json()["physician_id"] == old
        assert (await asyncio.wait_for(assign_task, 8)).status_code == 200
        async with client.session_factory() as db:
            assert (await db.get(StewardServiceInstance, iid)).physician_id == new["id"]
    finally:
        if assign_task is not None and not assign_task.done():
            assign_task.cancel()
            await asyncio.gather(assign_task, return_exceptions=True)


async def test_credential_expiry_during_instance_lock_wait_blocks_report(client, monkeypatch):
    ctx = await _closed_context(client)
    a, iid, pid = ctx["admin"], ctx["instance"]["id"], ctx["staff"]["physician"]["id"]
    before = datetime.now(timezone.utc)
    monkeypatch.setattr(prof, "now_utc", lambda: before)
    async with client.session_factory() as db:
        await db.execute(update(StewardStaff).where(StewardStaff.id == pid).values(
            credential_valid_until=before + timedelta(minutes=1)))
        await db.commit()
    real_guard, entered, state = prof.guard_physician_authorization, asyncio.Event(), {}

    async def record_guard(db, physician_id, admin_id=None):
        await real_guard(db, physician_id, admin_id)
        state["pid"] = await _pid(db)
        entered.set()

    monkeypatch.setattr(prof, "guard_physician_authorization", record_guard)
    async with client.session_factory() as holder:
        await holder.execute(update(StewardServiceInstance).where(StewardServiceInstance.id == iid).values(
            physician_id=StewardServiceInstance.physician_id))
        task = asyncio.create_task(client.post(f"{API}/admin/steward/reports", headers=a,
            json={"instance_id": iid, "title": "更正", "content": "等待时到期"}))
        try:
            await asyncio.wait_for(entered.wait(), 8)
            assert await _wait_for_lock(client, state["pid"])
            monkeypatch.setattr(prof, "now_utc", lambda: before + timedelta(minutes=2))
            await holder.commit()
            assert (await asyncio.wait_for(task, 8)).status_code == 409
            async with client.session_factory() as db:
                assert len((await db.execute(select(StewardServiceReport))).scalars().all()) == 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class PausedAdapter:
    def __init__(self, *, timeout=False):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.timeout = timeout

    async def place_call(self, order, script, number):
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), 8)
        if self.timeout:
            raise TimeoutError("供应商受理响应超时")
        return {"outcome": "accepted", "request_id": "REQ-NEW", "simulated": False}


async def _prepared_call(client, adapter):
    ctx = await _setup_with_phone_consents(client)
    a = ctx["admin"]
    sid = await _publish_script(client, a)
    oid = (await _create_order(client, a, ctx, sid)).json()["id"]
    phone.register_adapter("ccc", adapter)
    assert (await client.post(f"{API}/admin/phone/orders/{oid}/review", headers=a, json={"approve": True})).status_code == 200
    return ctx, oid


async def _deliver(client, payload):
    body, sig = _signed(payload)
    async with client.session_factory() as db:
        return await phone.handle_callback(db, body, sig)


async def _dispatch(client, oid):
    async with client.session_factory() as db:
        return await phone.dispatch_order(db, oid)


@pytest.mark.parametrize("with_attempt", [False, True])
@pytest.mark.parametrize("outcome,expected_status", [("completed", "completed"), ("no_answer", "queued")])
async def test_early_callbacks_are_persisted_reconciled_and_deduplicated(client, with_attempt, outcome, expected_status):
    adapter = PausedAdapter()
    _ctx, oid = await _prepared_call(client, adapter)
    dispatch = asyncio.create_task(_dispatch(client, oid))
    try:
        await asyncio.wait_for(adapter.entered.wait(), 8)
        payload = {"order_id": oid, "outcome": outcome, "request_id": "REQ-NEW", "event_id": "EARLY", "receipt_ref": "RECEIPT"}
        if with_attempt:
            payload["attempt_no"] = 1
        results = await asyncio.wait_for(asyncio.gather(*[_deliver(client, payload) for _ in range(3)]), 8)
        assert all(r["result"] == "pending_correlation" and not r["applied"] for r in results)
        async with client.session_factory() as db:
            events = (await db.execute(select(PhoneCallbackEvent))).scalars().all()
            assert len(events) == 1 and events[0].result == "pending_correlation"
            assert (await db.get(PhoneCallOrder, oid)).status == "in_progress"
        adapter.release.set()
        result = await asyncio.wait_for(dispatch, 8)
        assert result["status"] == expected_status and result["attempts"] == 1
        replay = await asyncio.gather(*[_deliver(client, payload) for _ in range(3)])
        assert all(r["result"] == "duplicate" and not r["applied"] for r in replay)
        async with client.session_factory() as db:
            event = (await db.execute(select(PhoneCallbackEvent))).scalar_one()
            attempt = (await db.execute(select(PhoneCallAttempt))).scalar_one()
            assert event.result == "applied" and attempt.outcome == outcome
            assert attempt.receipt_ref == "RECEIPT" and not attempt.simulated
            assert (await db.get(PhoneCallOrder, oid)).attempts == 1
    finally:
        adapter.release.set()
        await asyncio.gather(dispatch, return_exceptions=True)


async def test_dispatch_holds_result_lock_against_competing_callback(client, monkeypatch):
    """调度先获取结果锁：回调真实等待；两表按同一顺序完成，不可出现相反结果。"""
    adapter = PausedAdapter(timeout=True)
    _ctx, oid = await _prepared_call(client, adapter)
    callback, observed = None, {}
    payload = {"order_id": oid, "outcome": "completed", "attempt_no": 1, "event_id": "RACE"}
    async with client.session_factory() as db:
        real_execute = db.execute

        async def race(stmt, *args, **kwargs):
            nonlocal callback
            if isinstance(stmt, Update) and stmt.table.name == "phone_call_orders" and callback is None:
                status = next((getattr(value, "value", None) for key, value in (stmt._values or {}).items()
                               if getattr(key, "key", key) == "status"), None)
                if status == "queued":
                    started = asyncio.Event()
                    async def deliver():
                        body, sig = _signed(payload)
                        async with client.session_factory() as cb:
                            observed["pid"] = await _pid(cb)
                            started.set()
                            return await phone.handle_callback(cb, body, sig)
                    callback = asyncio.create_task(deliver())
                    await asyncio.wait_for(started.wait(), 8)
                    # 老实现无工单锁时回调会先完成，最终一致性断言就能暴露覆盖。
                    observed["blocked"] = await _wait_for_lock(client, observed["pid"], completed=callback)
            return await real_execute(stmt, *args, **kwargs)

        monkeypatch.setattr(db, "execute", race)
        dispatch = asyncio.create_task(phone.dispatch_order(db, oid))
        try:
            await asyncio.wait_for(adapter.entered.wait(), 8)
            adapter.release.set()
            result = await asyncio.wait_for(dispatch, 8)
            assert callback is not None
            reply = await asyncio.wait_for(callback, 8)
            assert observed["blocked"] and not reply["applied"]
            assert result["status"] == "queued" and reply["status"] == "queued"
        finally:
            adapter.release.set()
            for task in (dispatch, callback):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*[t for t in (dispatch, callback) if t is not None], return_exceptions=True)
    async with client.session_factory() as db:
        order = await db.get(PhoneCallOrder, oid)
        attempt = (await db.execute(select(PhoneCallAttempt))).scalar_one()
        assert order.status == "queued" and attempt.outcome == "no_answer"


async def test_callback_holds_result_lock_against_late_timeout(client, monkeypatch):
    """回调先获取结果锁：超时写回真实等待，最终保留完成回执和非模拟标记。"""
    adapter = PausedAdapter(timeout=True)
    _ctx, oid = await _prepared_call(client, adapter)
    held, release, ready, state = asyncio.Event(), asyncio.Event(), asyncio.Event(), {}
    body, sig = _signed({"order_id": oid, "outcome": "completed", "attempt_no": 1, "event_id": "WIN", "receipt_ref": "SIGNED"})
    real_locked_attempt = phone._locked_attempt

    async def dispatch():
        async with client.session_factory() as db:
            state["pid"] = await _pid(db)
            ready.set()
            return await phone.dispatch_order(db, oid)

    async def hold_callback(db, order_id, attempt_no):
        if asyncio.current_task() is callback:
            held.set()
            await asyncio.wait_for(release.wait(), 8)
        return await real_locked_attempt(db, order_id, attempt_no)

    monkeypatch.setattr(phone, "_locked_attempt", hold_callback)
    writer = asyncio.create_task(dispatch())
    callback = None
    try:
        await asyncio.wait_for(ready.wait(), 8)
        await asyncio.wait_for(adapter.entered.wait(), 8)
        async def deliver():
            async with client.session_factory() as db:
                return await phone.handle_callback(db, body, sig)
        callback = asyncio.create_task(deliver())
        await asyncio.wait_for(held.wait(), 8)
        adapter.release.set()
        assert await _wait_for_lock(client, state["pid"])
        release.set()
        result, reply = await asyncio.wait_for(asyncio.gather(writer, callback), 8)
        assert result["status"] == reply["status"] == "completed" and reply["applied"]
        async with client.session_factory() as db:
            attempt = (await db.execute(select(PhoneCallAttempt))).scalar_one()
            assert attempt.outcome == "completed" and attempt.receipt_ref == "SIGNED" and not attempt.simulated
            assert (await db.get(PhoneCallOrder, oid)).status == "completed"
    finally:
        release.set()
        adapter.release.set()
        for task in (writer, callback):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*[t for t in (writer, callback) if t is not None], return_exceptions=True)


@pytest.mark.parametrize("cancel", [False, True])
async def test_pending_unknown_request_is_not_applied_after_ack_or_cancel(client, cancel):
    adapter = PausedAdapter()
    ctx, oid = await _prepared_call(client, adapter)
    writer = asyncio.create_task(_dispatch(client, oid))
    try:
        await asyncio.wait_for(adapter.entered.wait(), 8)
        payload = {"order_id": oid, "outcome": "completed", "request_id": "UNRELATED", "event_id": "UNKNOWN"}
        assert (await _deliver(client, payload))["result"] == "pending_correlation"
        if cancel:
            assert (await client.post(f"{API}/admin/phone/orders/{oid}/cancel", headers=ctx["admin"],
                                      json={"reason": "用户撤回"})).status_code == 200
        adapter.release.set()
        result = await asyncio.wait_for(writer, 8)
        assert result["status"] == ("cancelled" if cancel else "in_progress")
        async with client.session_factory() as db:
            event = (await db.execute(select(PhoneCallbackEvent))).scalar_one()
            assert event.result == ("rejected_state" if cancel else "stale")
            assert (await db.execute(select(PhoneCallAttempt))).scalar_one().finished_at is None
    finally:
        adapter.release.set()
        await asyncio.gather(writer, return_exceptions=True)


async def test_same_event_id_cannot_replace_existing_payload(client):
    adapter = PausedAdapter()
    ctx, oid = await _prepared_call(client, adapter)
    writer = asyncio.create_task(_dispatch(client, oid))
    try:
        await asyncio.wait_for(adapter.entered.wait(), 8)
        payload = {"order_id": oid, "outcome": "completed", "request_id": "REQ-NEW", "event_id": "IMMUTABLE"}
        assert (await _deliver(client, payload))["result"] == "pending_correlation"
        # 同一业务事件换 JSON 键序重投仍可去重，不能把原始字节摘要当作业务身份。
        reordered = {**dict(reversed(list(payload.items()))), "sent_at": "retry-time"}
        assert (await _deliver(client, reordered))["result"] == "pending_correlation"
        body, sig = _signed({**payload, "outcome": "no_answer"})
        response = await client.post(f"{API}/admin/phone/callback", headers={**ctx["admin"], "X-Phone-Signature": sig,
                                      "Content-Type": "application/json"}, content=body)
        assert response.status_code == 409
        adapter.release.set()
        assert (await asyncio.wait_for(writer, 8))["status"] == "completed"
    finally:
        adapter.release.set()
        await asyncio.gather(writer, return_exceptions=True)


async def test_unknown_adapter_outcome_records_failure_without_losing_attempt(client):
    class UnknownAdapter:
        async def place_call(self, order, script, number):
            return {"outcome": "unrecognized_vendor_state", "simulated": False}
    _ctx, oid = await _prepared_call(client, UnknownAdapter())
    result = await _dispatch(client, oid)
    assert result["status"] == "failed"
    async with client.session_factory() as db:
        attempt = (await db.execute(select(PhoneCallAttempt))).scalar_one()
        assert attempt.outcome == "blocked" and attempt.finished_at is not None
        assert (await db.get(PhoneCallOrder, oid)).status == "failed"
