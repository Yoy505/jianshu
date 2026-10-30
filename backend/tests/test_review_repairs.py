"""2026-10-26 audit counterexamples, run with either --business-db backend."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.models import (
    StewardCarePlan, StewardProfessionalOrder, StewardServiceInstance, StewardServiceReport, StewardStaff, StewardTask,
)
from app.services import professional_service as prof, steward_service as svc
from tests.commerce_helpers import API, admin_headers, make_package, user_headers
from tests.steward_helpers import claim_entitlement, create_staff, issue_batch_entitlement
from tests.test_physician_identity_chain import _activate, _admin_id, _bind, _complete, _order

pytestmark = pytest.mark.asyncio


async def _review_task(client, ctx):
    async with client.session_factory() as db:
        return (await db.execute(select(StewardTask).where(
            StewardTask.instance_id == ctx["instance"]["id"], StewardTask.task_type == "final_review",
        ))).scalar_one()


def _review_body():
    return {"result": "医师复评", "actual_occurred_at": datetime.now(timezone.utc).isoformat(),
            "backfill_reason": "到期补录"}


async def test_historical_physician_tasks_and_plans_do_not_grant_access(client):
    ctx = await _activate(client)
    a, iid, old = ctx["admin"], ctx["instance"]["id"], ctx["staff"]["physician"]["id"]
    await _bind(client, a, old, await _admin_id(client, "editor"))
    e = await admin_headers(client, "editor")
    assert (await client.get(f"{API}/admin/steward/instances/{iid}", headers=e)).status_code == 200
    new = await create_staff(client, a, "physician", "改派医师")
    assert (await client.post(f"{API}/admin/steward/instances/{iid}/assign",
                              json={"physician_id": new["id"]}, headers=a)).status_code == 200
    # Both pending and completed tasks, and even an unrevoked old plan, are history only.
    for status in ("pending", "done"):
        async with client.session_factory() as db:
            await db.execute(update(StewardTask).where(StewardTask.physician_id == old).values(status=status))
            await db.commit()
        assert (await client.get(f"{API}/admin/steward/instances/{iid}", headers=e)).status_code == 403
        assert (await client.get(f"{API}/admin/steward/instances", headers=e)).json()["total"] == 0
        for path in ("care-plans", "reports"):
            assert (await client.get(f"{API}/admin/steward/{path}", headers=e)).json() == []
    assert (await client.post(f"{API}/admin/steward/care-plans/{ctx['care_plan']['id']}/revoke",
                              json={"reason": "改派"}, headers=a)).status_code == 200
    assert (await client.get(f"{API}/admin/steward/instances/{iid}/timeline", headers=e)).status_code == 403


@pytest.mark.parametrize("invalid", ["revoked", "expired", "unverified"])
async def test_invalid_credential_blocks_medical_actions_and_trusted_plans(client, invalid):
    ctx = await _activate(client)
    a, pid = ctx["admin"], ctx["staff"]["physician"]["id"]
    link = await _bind(client, a, pid, await _admin_id(client))
    oid = await _order(client, a, ctx["instance"]["id"], pid)
    task = await _review_task(client, ctx)
    now = datetime.now(timezone.utc)
    values = {"revoked": {"credential_revoked_at": now},
              "expired": {"credential_valid_until": now - timedelta(seconds=1)},
              "unverified": {"credential_verified_at": None}}[invalid]
    async with client.session_factory() as db:
        await db.execute(update(StewardStaff).where(StewardStaff.id == pid).values(**values))
        await db.commit()
    assert (await _complete(client, a, oid)).status_code == 403
    assert (await client.post(f"{API}/admin/steward/tasks/{task.id}/result", json=_review_body(), headers=a)).status_code == 403
    plan = {"user_id": 1, "physician_id": pid, "org_name": "杏临汇签约服务机构", "version": "v2", "content": {}}
    assert (await client.post(f"{API}/admin/steward/care-plans", json=plan, headers=a)).status_code == 403
    # External verification cannot bypass credential validity either.
    assert (await client.delete(f"{API}/admin/steward/staff-links/{link}", headers=a)).status_code == 200
    assert (await client.post(f"{API}/admin/steward/care-plans", json={**plan, "external_evidence": {
        "evidence_ref": "private://x", "evidence_source": "机构"}}, headers=a)).status_code == 403
    async with client.session_factory() as db:
        assert (await db.get(StewardTask, task.id)).status == "pending"
        assert not (await db.execute(select(StewardCarePlan).where(StewardCarePlan.version == "v2"))).scalars().all()


async def test_ops_cannot_complete_review_or_issue_closure_report(client):
    ctx = await _activate(client)
    a, iid, pid = ctx["admin"], ctx["instance"]["id"], ctx["staff"]["physician"]["id"]
    task = await _review_task(client, ctx)
    async with client.session_factory() as db:
        await db.execute(update(StewardServiceInstance).where(StewardServiceInstance.id == iid).values(
            ends_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        await db.commit()
    assert (await client.post(f"{API}/admin/steward/tasks/{task.id}/result", json=_review_body(), headers=a)).status_code == 403
    # A historically completed review does not let ops produce a physician signature.
    async with client.session_factory() as db:
        await db.execute(update(StewardTask).where(StewardTask.id == task.id).values(
            status="done", completed_by_admin_id=await _admin_id(client)))
        await db.commit()
    body = {"closure_report": "结案"}
    assert (await client.post(f"{API}/admin/steward/instances/{iid}/close", json=body, headers=a)).status_code == 403
    await _bind(client, a, pid, await _admin_id(client))
    assert (await client.post(f"{API}/admin/steward/instances/{iid}/close", json=body, headers=a)).status_code == 409
    async with client.session_factory() as db:
        assert not (await db.execute(select(StewardServiceReport))).scalars().all()
        await db.execute(update(StewardTask).where(StewardTask.id == task.id).values(status="pending"))
        await db.commit()
    assert (await client.post(f"{API}/admin/steward/tasks/{task.id}/result", json=_review_body(), headers=a)).status_code == 200
    assert (await client.post(f"{API}/admin/steward/instances/{iid}/close", json=body, headers=a)).status_code == 200
    async with client.session_factory() as db:
        report = (await db.execute(select(StewardServiceReport))).scalar_one()
        assert report.physician_id == pid and report.created_by_admin_id == await _admin_id(client)


async def test_legacy_review_can_be_attested_without_overwriting_history(client):
    ctx = await _activate(client)
    a, iid, pid = ctx["admin"], ctx["instance"]["id"], ctx["staff"]["physician"]["id"]
    task = await _review_task(client, ctx)
    async with client.session_factory() as db:
        await db.execute(update(StewardTask).where(StewardTask.id == task.id).values(status="done", result="旧运营录入"))
        await db.execute(update(StewardServiceInstance).where(StewardServiceInstance.id == iid).values(
            ends_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        await db.commit()
    await _bind(client, a, pid, await _admin_id(client))
    resp = await client.post(f"{API}/admin/steward/professional/orders", headers=a, json={
        "instance_id": iid, "physician_id": pid, "order_type": "final_review", "title": "当前医师复核",
        "task_id": task.id,
    })
    assert resp.status_code == 201, resp.text
    oid = resp.json()["id"]
    async with client.session_factory() as db:
        # 历史 result_meta 原来可由客户端提供，不能把其中伪造的签名键当成签发证据。
        await db.execute(update(StewardProfessionalOrder).where(StewardProfessionalOrder.id == oid).values(
            status="done", result_meta={"physician_signature": {"physician_id": pid, "admin_id": await _admin_id(client)}}))
        await db.commit()
    assert (await client.post(f"{API}/admin/steward/instances/{iid}/close", headers=a,
                              json={"closure_report": "伪造签名"})).status_code == 409
    async with client.session_factory() as db:
        await db.execute(update(StewardProfessionalOrder).where(StewardProfessionalOrder.id == oid).values(status="pending"))
        await db.commit()
    assert (await _complete(client, a, oid)).status_code == 200
    assert (await client.post(f"{API}/admin/steward/instances/{iid}/close", headers=a,
                              json={"closure_report": "医师复核后结案"})).status_code == 200
    async with client.session_factory() as db:
        assert (await db.get(StewardTask, task.id)).result == "旧运营录入"


@pytest.mark.parametrize("invalid", ["revoked", "expired"])
async def test_credential_loss_blocks_closure_and_correction_after_signed_review(client, invalid):
    ctx = await _activate(client)
    a, iid, pid = ctx["admin"], ctx["instance"]["id"], ctx["staff"]["physician"]["id"]
    await _bind(client, a, pid, await _admin_id(client))
    task = await _review_task(client, ctx)
    assert (await client.post(f"{API}/admin/steward/tasks/{task.id}/result", json=_review_body(), headers=a)).status_code == 200
    async with client.session_factory() as db:
        await db.execute(update(StewardServiceInstance).where(StewardServiceInstance.id == iid).values(
            ends_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        values = {"credential_revoked_at": datetime.now(timezone.utc)} if invalid == "revoked" else {
            "credential_valid_until": datetime.now(timezone.utc) - timedelta(seconds=1)}
        await db.execute(update(StewardStaff).where(StewardStaff.id == pid).values(**values))
        await db.commit()
    assert (await client.post(f"{API}/admin/steward/instances/{iid}/close", json={"closure_report": "结案"}, headers=a)).status_code == 403
    async with client.session_factory() as db:
        await db.execute(update(StewardServiceInstance).where(StewardServiceInstance.id == iid).values(status="closed"))
        await db.commit()
    assert (await client.post(f"{API}/admin/steward/reports", json={"instance_id": iid, "title": "更正", "content": "更正"}, headers=a)).status_code == 403
    async with client.session_factory() as db:
        assert not (await db.execute(select(StewardServiceReport))).scalars().all()


async def test_credential_revoked_between_identity_check_and_write(client, monkeypatch):
    ctx = await _activate(client)
    a, pid = ctx["admin"], ctx["staff"]["physician"]["id"]
    await _bind(client, a, pid, await _admin_id(client))
    oid = await _order(client, a, ctx["instance"]["id"], pid)
    real_check = prof.require_physician_identity

    async def revoke_after_check(db, admin, physician_id, *, action):
        physician = await real_check(db, admin, physician_id, action=action)
        async with client.session_factory() as other:
            await other.execute(update(StewardStaff).where(StewardStaff.id == pid).values(
                credential_revoked_at=datetime.now(timezone.utc)))
            await other.commit()
        return physician

    monkeypatch.setattr(prof, "require_physician_identity", revoke_after_check)
    assert (await _complete(client, a, oid)).status_code == 409


@pytest.mark.parametrize("config", [None, {}, {"template_version": "custom_v1"}, {"followup_days": []}])
async def test_purchase_rule_snapshot_survives_global_change_before_claim(client, monkeypatch, config):
    a = await admin_headers(client)
    monkeypatch.setattr(settings, "STEWARD_FOLLOWUP_DAYS", "7,14,21,28,35,42")
    pkg = await make_package(client, a, service_days=30, fulfillment_config=config)
    ent = await issue_batch_entitlement(client, a, pkg)
    monkeypatch.setattr(settings, "STEWARD_FOLLOWUP_DAYS", "3,6,9")
    await claim_entitlement(client, ent)
    await client.get(f"{API}/mp/steward/instances", headers=user_headers(1))
    async with client.session_factory() as db:
        instance = (await db.execute(select(StewardServiceInstance))).scalar_one()
        expected = [] if config == {"followup_days": []} else [7, 14, 21, 28]
        assert svc.instance_followup_days(instance) == expected
        assert instance.fulfillment_config["template_version"] == (config or {}).get("template_version", "steward_v1")
