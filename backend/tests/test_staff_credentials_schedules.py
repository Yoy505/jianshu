"""C2 资质核验与排班覆盖：激活门禁 staffing_ready 要求三岗在职 + 资质有效，管家两岗当日有有效排班。"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.models import StewardStaff
from tests.commerce_helpers import API
from tests.steward_helpers import create_staff, full_setup

pytestmark = pytest.mark.asyncio
CRED = {"credential_ref": "private://credentials/x.pdf", "credential_source": "机构核验件"}


async def _staffing(client, ctx) -> dict:
    resp = await client.get(f"{API}/mp/steward/instances/{ctx['instance']['id']}", headers=ctx["user"])
    assert resp.status_code == 200, resp.text
    return next(c for c in resp.json()["prep_checks"] if c["check_type"] == "staffing_ready")


async def test_gate_requires_valid_credential_and_schedule(client):
    ctx = await full_setup(client)
    a = ctx["admin"]
    assert (await _staffing(client, ctx))["status"] == "passed"

    raw = await create_staff(client, a, "butler", "未核验管家", ready=False)
    assert raw["credential_valid"] is False
    resp = await client.post(f"{API}/admin/steward/instances/{ctx['instance']['id']}/assign",
                             json={"butler_id": raw["id"]}, headers=a)
    assert resp.status_code == 200, resp.text
    check = await _staffing(client, ctx)
    assert check["status"] != "passed"
    assert "负责管家资质未核验" in check["missing_reason"] and "负责管家当日无有效排班" in check["missing_reason"]
    resp = await client.post(f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"])
    assert resp.status_code == 409 and "staffing_ready" in resp.json()["detail"]["missing"]

    # 核验资质（带未来有效期）→ 仍缺排班；排班后通过
    future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()
    resp = await client.post(f"{API}/admin/steward/staff/{raw['id']}/credential/verify",
                             json={**CRED, "valid_until": future, "service_scope": "睡眠健康管家"}, headers=a)
    assert resp.status_code == 200 and resp.json()["credential_valid"] is True
    assert resp.json()["service_scope"] == "睡眠健康管家" and "credential_ref" not in resp.json()
    check = await _staffing(client, ctx)
    assert "资质" not in check["missing_reason"] and "无有效排班" in check["missing_reason"]
    resp = await client.post(f"{API}/admin/steward/staff/{raw['id']}/schedules",
                             json={"starts_on": (date.today() - timedelta(days=1)).isoformat(),
                                   "ends_on": (date.today() + timedelta(days=60)).isoformat()}, headers=a)
    assert resp.status_code == 201, resp.text
    schedule_id = resp.json()["id"]
    assert (await _staffing(client, ctx))["status"] == "passed"

    # 排班取消 → 立即重判缺项；再排 → 通过
    resp = await client.post(f"{API}/admin/steward/schedules/{schedule_id}/cancel", json={"reason": "请假"}, headers=a)
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    assert "无有效排班" in (await _staffing(client, ctx))["missing_reason"]
    assert (await client.post(f"{API}/admin/steward/schedules/{schedule_id}/cancel",
                              json={"reason": "重复"}, headers=a)).status_code == 409
    resp = await client.post(f"{API}/admin/steward/staff/{raw['id']}/schedules",
                             json={"starts_on": (date.today() - timedelta(days=1)).isoformat()}, headers=a)
    assert resp.status_code == 201
    assert (await _staffing(client, ctx))["status"] == "passed"

    # 医师资质撤销 → 缺项；重新核验 → 通过；资质到期（直接改库）→ 缺项
    phys_id = ctx["staff"]["physician"]["id"]
    resp = await client.post(f"{API}/admin/steward/staff/{phys_id}/credential/revoke",
                             json={"reason": "执业证到期待复核"}, headers=a)
    assert resp.status_code == 200 and resp.json()["credential_valid"] is False
    assert "签约医师资质" in (await _staffing(client, ctx))["missing_reason"]
    assert (await client.post(f"{API}/admin/steward/staff/{phys_id}/credential/revoke",
                              json={"reason": "again"}, headers=a)).status_code == 409
    resp = await client.post(f"{API}/admin/steward/staff/{phys_id}/credential/verify", json=CRED, headers=a)
    assert resp.status_code == 200 and resp.json()["credential_revoked_at"] is None
    assert (await _staffing(client, ctx))["status"] == "passed"
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        await session.execute(update(StewardStaff).where(StewardStaff.id == phys_id)
                              .values(credential_valid_until=datetime.now(timezone.utc) - timedelta(days=1)))
        await session.commit()
    assert "签约医师资质" in (await _staffing(client, ctx))["missing_reason"]
    # 医师不要求排班：从未给医师排班，此前门禁仍通过（上面已验证）


async def test_validation_rules(client):
    ctx = await full_setup(client)
    a = ctx["admin"]
    staff = await create_staff(client, a, "butler", "校验管家", ready=False)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert (await client.post(f"{API}/admin/steward/staff/{staff['id']}/credential/verify",
                              json={**CRED, "valid_until": past}, headers=a)).status_code == 422
    assert (await client.post(f"{API}/admin/steward/staff/{staff['id']}/credential/revoke",
                              json={"reason": "x"}, headers=a)).status_code == 409  # 尚无资质
    assert (await client.post(f"{API}/admin/steward/staff/{staff['id']}/schedules",
                              json={"starts_on": "2026-11-10", "ends_on": "2026-11-01"}, headers=a)).status_code == 422
    resp = await client.patch(f"{API}/admin/steward/staff/{staff['id']}", json={"status": "inactive"}, headers=a)
    assert resp.status_code == 200
    assert (await client.post(f"{API}/admin/steward/staff/{staff['id']}/schedules",
                              json={"starts_on": "2026-11-01"}, headers=a)).status_code == 422
    assert (await client.post(f"{API}/admin/steward/staff/{staff['id']}/credential/verify",
                              json=CRED, headers=a)).status_code == 422
    resp = await client.get(f"{API}/admin/steward/staff/{ctx['staff']['butler']['id']}/schedules", headers=a)
    assert resp.status_code == 200 and resp.json() and resp.json()[0]["status"] == "active"
    assert (await client.get(f"{API}/admin/steward/staff/999999/schedules", headers=a)).status_code == 404
