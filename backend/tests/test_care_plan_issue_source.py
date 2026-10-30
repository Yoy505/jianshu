"""C3 签发凭据与激活证据：签发来源分离（运营录入 / 医师本人 / 外部凭据核验）。

运营录入（ops_entry）的计划不能通过激活门禁、不能作为医学判断类工单依据；补外部签发材料并由
核验责任人核验后升级为 external_verified；绑定医师本人账号签发为 physician_self；激活凭证固化
计划 id、来源与责任人快照。
"""

import pytest
from sqlalchemy import select

from app.models import AdminUser, StewardActivationCredential
from tests.commerce_helpers import API
from tests.steward_helpers import USER1, full_setup

pytestmark = pytest.mark.asyncio

EVIDENCE = {"evidence_ref": "private://care-plans/u1-v2.pdf", "evidence_source": "机构签发件", "note": "纸质件已归档"}


async def _plan_check(client, ctx) -> dict:
    resp = await client.get(f"{API}/mp/steward/instances/{ctx['instance']['id']}", headers=ctx["user"])
    assert resp.status_code == 200, resp.text
    return next(c for c in resp.json()["prep_checks"] if c["check_type"] == "physician_plan")


async def _create_plan(client, a, physician_id: int, version: str, **extra):
    return await client.post(
        f"{API}/admin/steward/care-plans",
        json={"user_id": USER1["id"], "physician_id": physician_id, "version": version,
              "org_name": "杏临汇签约服务机构", "content": {"focus": version}, **extra},
        headers=a,
    )


async def test_ops_entry_plan_blocks_activation_and_medical_orders_until_verified(client):
    ctx = await full_setup(client)
    a = ctx["admin"]
    physician_id = ctx["staff"]["physician"]["id"]
    assert (await _plan_check(client, ctx))["status"] == "passed"  # full_setup 的计划为 external_verified

    # 撤销可信计划 → 门禁缺失
    resp = await client.post(f"{API}/admin/steward/care-plans/{ctx['care_plan']['id']}/revoke",
                             json={"reason": "重新签发"}, headers=a)
    assert resp.status_code == 200
    assert (await _plan_check(client, ctx))["status"] != "passed"

    # 运营账号（未绑定医师、无外部材料）录入 v2 → ops_entry，不可信
    resp = await _create_plan(client, a, physician_id, "v2")
    assert resp.status_code == 201, resp.text
    plan = resp.json()
    assert plan["issue_source"] == "ops_entry" and plan["trusted"] is False and plan["verified_by_admin_id"] is None
    check = await _plan_check(client, ctx)
    assert check["status"] != "passed" and "运营录入" in (check["missing_reason"] or "")
    resp = await client.post(f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"])
    assert resp.status_code == 409 and "physician_plan" in resp.json()["detail"]["missing"]
    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": ctx["instance"]["id"], "order_type": "physician_review", "title": "复核",
              "priority": "normal", "physician_id": physician_id},
        headers=a,
    )
    assert resp.status_code == 422  # 无可信签发 → 不能生成医学判断类工单

    # 核验：缺材料 422；补外部签发材料 → external_verified，核验责任人为当前账号
    resp = await client.post(f"{API}/admin/steward/care-plans/{plan['id']}/verify", json={}, headers=a)
    assert resp.status_code == 422
    resp = await client.post(f"{API}/admin/steward/care-plans/{plan['id']}/verify", json=EVIDENCE, headers=a)
    assert resp.status_code == 200, resp.text
    verified = resp.json()
    assert verified["issue_source"] == "external_verified" and verified["trusted"] is True
    assert verified["evidence_source"] == "机构签发件" and verified["verified_by_admin_id"] and verified["verified_at"]
    assert "evidence_ref" not in verified  # 私有材料引用不对外返回
    assert (await _plan_check(client, ctx))["status"] == "passed"

    # 激活 → 凭证固化真实计划、来源与责任人
    resp = await client.post(f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"])
    assert resp.status_code == 200, resp.text
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        cred = (await session.execute(select(StewardActivationCredential).where(
            StewardActivationCredential.instance_id == ctx["instance"]["id"]))).scalar_one()
        assert cred.plan_id == plan["id"] and cred.plan_issue_source == "external_verified"
        assert cred.plan_version == "v2"
        snap = cred.responsibility_snapshot
        assert snap["plan"]["verified_by_admin_id"] == verified["verified_by_admin_id"]
        assert "delivery" in snap["manual_checks"] and snap["manual_checks"]["delivery"]["completed_by_admin_id"]
    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": ctx["instance"]["id"], "order_type": "physician_review", "title": "复核",
              "priority": "normal", "physician_id": physician_id},
        headers=a,
    )
    assert resp.status_code == 201 and resp.json()["care_plan_version"] == "v2"


async def test_bound_physician_account_issues_physician_self_and_verify_rules(client):
    ctx = await full_setup(client)
    a = ctx["admin"]
    physician_id = ctx["staff"]["physician"]["id"]
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        admin_id = (await session.execute(select(AdminUser).where(AdminUser.username == "admin"))).scalar_one().id
    resp = await client.post(f"{API}/admin/steward/staff-links",
                             json={"staff_id": physician_id, "admin_id": admin_id}, headers=a)
    assert resp.status_code == 201
    resp = await _create_plan(client, a, physician_id, "v2")
    assert resp.status_code == 201 and resp.json()["issue_source"] == "physician_self" and resp.json()["trusted"]
    self_plan = resp.json()
    # 医师本人签发无需外部核验
    resp = await client.post(f"{API}/admin/steward/care-plans/{self_plan['id']}/verify", json=EVIDENCE, headers=a)
    assert resp.status_code == 409
    # 已撤销计划不能核验
    resp = await client.post(f"{API}/admin/steward/care-plans/{self_plan['id']}/revoke",
                             json={"reason": "录错"}, headers=a)
    assert resp.status_code == 200
    resp = await client.post(f"{API}/admin/steward/care-plans/{self_plan['id']}/verify", json=EVIDENCE, headers=a)
    assert resp.status_code == 409
    # 列表返回来源字段
    resp = await client.get(f"{API}/admin/steward/care-plans", params={"user_id": USER1["id"]}, headers=a)
    sources = {p["version"]: p["issue_source"] for p in resp.json()}
    assert sources["v1"] == "external_verified" and sources["v2"] == "physician_self"
