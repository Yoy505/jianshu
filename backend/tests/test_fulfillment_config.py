"""F03 商品履约配置（批次 C1）：实例只为适用商品创建，服务期与模板取版本快照。

覆盖任务书验收点：不进入履约的商品、不同服务期、同商品历史版本、重复领取、
validity_days 与 service_days 独立、随访节点按快照、输入校验。
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import steward_service as svc
from tests.commerce_helpers import API, admin_headers, make_package, user_headers
from tests.steward_helpers import USER1, claim_entitlement, issue_batch_entitlement


async def _instances(client) -> list[dict]:
    resp = await client.get(f"{API}/mp/steward/instances", headers=user_headers(USER1["id"]))
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_plain_product_issues_entitlement_without_steward_instance(client):
    a = await admin_headers(client)
    pkg = await make_package(client, a, name="通用健康服务包 · 30 天", validity_days=30, fulfillment_type="none")
    detail = (await client.get(f"{API}/mp/packages/{pkg['package_id']}", headers=user_headers(USER1["id"]))).json()
    assert detail["fulfillment_type"] == "none" and detail["service_days"] is None

    ent_id = await issue_batch_entitlement(client, a, pkg)
    ent = await claim_entitlement(client, ent_id)
    assert ent["status"] == "active" and ent["validity_days"] == 30
    assert await _instances(client) == []  # 不生成 45 天管家实例
    assert await _instances(client) == []  # 重复读取不会补建


@pytest.mark.asyncio
async def test_steward_product_snapshots_service_days_and_history_version(client):
    a = await admin_headers(client)
    config = {"template_version": "general_30d_v1", "followup_days": [5, 10, 20]}
    pkg = await make_package(
        client, a, name="管家服务包 · 30 天", validity_days=90, service_days=30, fulfillment_config=config
    )
    detail = (await client.get(f"{API}/mp/packages/{pkg['package_id']}", headers=user_headers(USER1["id"]))).json()
    assert detail["fulfillment_type"] == "steward" and detail["service_days"] == 30 and detail["validity_days"] == 90

    ent_id = await claim_entitlement(client, await issue_batch_entitlement(client, a, pkg))
    first = await _instances(client)
    assert len(first) == 1
    inst = first[0]
    assert inst["service_days"] == 30 and inst["validity_days"] == 90  # 两字段独立，不互换
    assert len(await _instances(client)) == 1  # 重复领取 / 重复读取只有一份实例

    # 同商品发布 v2（服务期 20 天）并上架：历史实例仍按 v1 的 30 天解释，新权益按 v2
    resp = await client.post(
        f"{API}/admin/mp/packages/{pkg['package_id']}/versions",
        json={
            "price_cents": 9900, "validity_days": 60,
            "benefits": [{"name": "健康管家服务"}], "service_desc": "v2 说明", "terms": "v2 售后",
            "fulfillment_type": "steward", "service_days": 20,
        },
        headers=a,
    )
    assert resp.status_code == 201, resp.text
    v2 = resp.json()
    assert v2["service_days"] == 20 and v2["fulfillment_config"] == {"template_version": "steward_v1", "followup_days": [7, 14]}
    resp = await client.post(
        f"{API}/admin/mp/packages/{pkg['package_id']}/shelf",
        json={"on_shelf": True, "version_id": v2["id"]}, headers=a,
    )
    assert resp.status_code == 200, resp.text
    await claim_entitlement(
        client, await issue_batch_entitlement(client, a, {"package_id": pkg["package_id"], "version_id": v2["id"]})
    )
    rows = sorted(await _instances(client), key=lambda r: r["id"])
    assert [r["service_days"] for r in rows] == [30, 20]
    assert [r["validity_days"] for r in rows] == [90, 60]


@pytest.mark.asyncio
async def test_version_input_validation_and_controlled_default(client):
    a = await admin_headers(client)
    resp = await client.post(f"{API}/admin/mp/packages", json={"name": "校验用服务包"}, headers=a)
    pid = resp.json()["id"]
    base = {"price_cents": 100, "validity_days": 30, "benefits": [{"name": "x"}], "service_desc": "d", "terms": "t"}

    # 普通商品不能带服务期 / 模板
    resp = await client.post(f"{API}/admin/mp/packages/{pid}/versions",
                             json={**base, "fulfillment_type": "none", "service_days": 30}, headers=a)
    assert resp.status_code == 422
    # 随访节点超过服务期
    resp = await client.post(
        f"{API}/admin/mp/packages/{pid}/versions",
        json={**base, "fulfillment_type": "steward", "service_days": 10,
              "fulfillment_config": {"followup_days": [7, 14]}},
        headers=a,
    )
    assert resp.status_code == 422
    # 随访节点未递增
    resp = await client.post(
        f"{API}/admin/mp/packages/{pid}/versions",
        json={**base, "fulfillment_type": "steward", "service_days": 30,
              "fulfillment_config": {"followup_days": [7, 7]}},
        headers=a,
    )
    assert resp.status_code == 422
    # steward 未传服务期 → 以全局 STEWARD_SERVICE_DAYS 作受控默认写入版本快照
    resp = await client.post(f"{API}/admin/mp/packages/{pid}/versions",
                             json={**base, "fulfillment_type": "steward"}, headers=a)
    assert resp.status_code == 201, resp.text
    assert resp.json()["service_days"] == 45 and resp.json()["fulfillment_type"] == "steward"
    # 省略 fulfillment_type → 默认普通商品
    resp = await client.post(f"{API}/admin/mp/packages/{pid}/versions", json=base, headers=a)
    assert resp.status_code == 201 and resp.json()["fulfillment_type"] == "none"


def test_task_specs_follow_snapshot_config():
    """任务规格按实例快照：随访节点取 fulfillment_config，历史缺项按固定兼容规则并按服务期截断。"""
    activated = datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)
    snap = SimpleNamespace(
        service_days=30, activated_at=activated, butler_id=None, physician_id=None,
        fulfillment_config={"template_version": "general_30d_v1", "followup_days": [5, 10, 20]},
    )
    follow = [s["service_day"] for s in svc.build_task_specs(snap) if s["task_type"] == "follow_up"]
    assert follow == [5, 10, 20]
    legacy = SimpleNamespace(service_days=30, activated_at=activated, butler_id=None, physician_id=None, fulfillment_config=None)
    follow = [s["service_day"] for s in svc.build_task_specs(legacy) if s["task_type"] == "follow_up"]
    assert follow == [7, 14, 21, 28]  # 固定兼容节点按 30 天截断，不读取运行期全局参数
    daily = [s for s in svc.build_task_specs(snap) if s["task_type"] == "daily"]
    assert len(daily) == 30
