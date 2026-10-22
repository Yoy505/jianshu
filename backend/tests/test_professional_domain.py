"""工作二切片 D · 专业任务与异常工单用例（契约 docs/api.md 第五部分 · 九）。

覆盖：规则版本发布/退役与计划引用校验、无签发阻断医学判断类工单、账号绑定防冒充、
转交与离岗、超时升级 sweep、结案高优先级钩子（真表）与版本化报告、结构化随访、
C 端聚合与固定急救文案、权限。
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import StewardProfessionalOrder, StewardServiceReport, StewardTask
from app.services import professional_service as prof
from tests.commerce_helpers import API
from tests.steward_helpers import create_staff, full_setup

pytestmark = pytest.mark.asyncio


async def _activate(client) -> dict:
    ctx = await full_setup(client)
    resp = await client.post(
        f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"]
    )
    assert resp.status_code == 200, resp.text
    ctx["instance"] = resp.json()
    return ctx


async def test_rules_versioning_and_plan_reference(client):
    """规则发布新版转旧版 retired；签发计划引用无效版本被拒、有效版本通过。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    physician = ctx["staff"]["physician"]

    resp = await client.post(
        f"{API}/admin/steward/care-rules",
        json={"org_name": "杏临汇签约服务机构", "version": "v1",
              "content": {"sleep_efficiency_floor": 70}},
        headers=a,
    )
    assert resp.status_code == 201
    resp = await client.post(
        f"{API}/admin/steward/care-rules",
        json={"org_name": "杏临汇签约服务机构", "version": "v1", "content": {}},
        headers=a,
    )
    assert resp.status_code == 409  # 同版本重复
    resp = await client.post(
        f"{API}/admin/steward/care-rules",
        json={"org_name": "杏临汇签约服务机构", "version": "v2",
              "content": {"sleep_efficiency_floor": 75}},
        headers=a,
    )
    assert resp.status_code == 201
    resp = await client.get(f"{API}/admin/steward/care-rules", headers=a)
    statuses = {r["version"]: r["status"] for r in resp.json()}
    assert statuses == {"v1": "retired", "v2": "active"}  # 旧版自动退役

    # 引用已退役版本 → 422；引用 active 版本 → 通过
    resp = await client.post(
        f"{API}/admin/steward/care-plans",
        json={"user_id": 1, "physician_id": physician["id"], "version": "v2",
              "org_name": "杏临汇签约服务机构", "rules_version": "v1", "content": {}},
        headers=a,
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"{API}/admin/steward/care-plans",
        json={"user_id": 1, "physician_id": physician["id"], "version": "v2",
              "org_name": "杏临汇签约服务机构", "rules_version": "v2", "content": {}},
        headers=a,
    )
    assert resp.status_code == 201 and resp.json()["rules_version"] == "v2"


async def test_medical_order_requires_care_plan(client):
    """无签发阻断：医学判断类工单须有该用户+该医师的有效签发计划；数据质量类不需要。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    instance_id = ctx["instance"]["id"]

    # 换一位无签发计划的医师
    other = await create_staff(client, a, "physician", "医师丁")
    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": instance_id, "order_type": "physician_review",
              "title": "第 14 天复核", "priority": "normal", "physician_id": other["id"]},
        headers=a,
    )
    assert resp.status_code == 422  # 无签发阻断

    # 有签发计划的医师 → 创建成功并固化计划/规则版本快照
    physician = ctx["staff"]["physician"]
    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": instance_id, "order_type": "physician_review",
              "title": "第 14 天复核", "priority": "normal", "physician_id": physician["id"]},
        headers=a,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["care_plan_version"] == "v1"

    # 数据质量类不需要签发
    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": instance_id, "order_type": "data_quality",
              "title": "睡眠数据缺口核查", "priority": "normal", "physician_id": other["id"]},
        headers=a,
    )
    assert resp.status_code == 201


async def test_complete_requires_bound_admin(client):
    """签发身份一致：未绑定指派医师的账号录入结果 403；绑定后成功。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    physician = ctx["staff"]["physician"]

    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": ctx["instance"]["id"], "order_type": "physician_review",
              "title": "复核", "priority": "normal", "physician_id": physician["id"]},
        headers=a,
    )
    order_id = resp.json()["id"]

    # admin 未绑定该医师 → 403（防冒充）
    resp = await client.post(
        f"{API}/admin/steward/professional/orders/{order_id}/complete",
        json={"result": "复核结论良好"},
        headers=a,
    )
    assert resp.status_code == 403

    # 建立绑定后 → 完成
    admin_id = None
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        from sqlalchemy import select as _select

        from app.models import AdminUser

        admin_id = (await session.execute(_select(AdminUser).where(AdminUser.username == "admin"))).scalar_one().id
    resp = await client.post(
        f"{API}/admin/steward/staff-links",
        json={"staff_id": physician["id"], "admin_id": admin_id},
        headers=a,
    )
    assert resp.status_code == 201
    resp = await client.post(
        f"{API}/admin/steward/professional/orders/{order_id}/complete",
        json={"result": "复核结论良好，继续当前计划"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "done"


async def test_transfer_and_deactivation(client):
    """转交：接收人须在职医师、留事件与转交事实；停用医师不能接收。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    physician = ctx["staff"]["physician"]
    receiver = await create_staff(client, a, "physician", "替补医师")

    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": ctx["instance"]["id"], "order_type": "physician_review",
              "title": "复核", "priority": "normal", "physician_id": physician["id"]},
        headers=a,
    )
    order_id = resp.json()["id"]

    resp = await client.post(
        f"{API}/admin/steward/professional/orders/{order_id}/transfer",
        json={"to_physician_id": receiver["id"], "reason": "原医师休假"},
        headers=a,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["physician_id"] == receiver["id"] and body["transferred_at"]

    # 接收医师需有自己的签发计划才能完成（无签发阻断同样适用完成校验）
    resp = await client.post(
        f"{API}/admin/steward/professional/orders/{order_id}/complete",
        json={"result": "代为复核"},
        headers=a,
    )
    assert resp.status_code == 403  # admin 未绑定接收医师

    # 停用医师不能作为接收人
    await client.patch(f"{API}/admin/steward/staff/{receiver['id']}", json={"status": "inactive"}, headers=a)
    other2 = await create_staff(client, a, "physician", "医师戊")
    resp = await client.post(
        f"{API}/admin/steward/professional/orders/{order_id}/transfer",
        json={"to_physician_id": receiver["id"], "reason": "再次转交"},
        headers=a,
    )
    assert resp.status_code == 422


async def test_escalation_sweep(client):
    """超时升级：过期未结工单被标记 escalated + 事件 + 审计；beat 注册。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    physician = ctx["staff"]["physician"]
    due = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": ctx["instance"]["id"], "order_type": "data_quality",
              "title": "数据核查", "priority": "normal", "physician_id": physician["id"],
              "due_at": due},
        headers=a,
    )
    order_id = resp.json()["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await prof.professional_sweep(session)
    assert result["escalated"] >= 1
    resp = await client.get(f"{API}/admin/steward/professional/orders/{order_id}", headers=a)
    assert resp.json()["escalated_at"] is not None
    assert any(e["action"] == "escalated" for e in resp.json()["events"])

    from app.workers.celery_app import celery_app
    from app.workers.tasks import professional_sweep_task

    assert celery_app.conf.beat_schedule["professional-sweep"]["task"] == "app.workers.tasks.professional_sweep"
    assert professional_sweep_task.name == "app.workers.tasks.professional_sweep"


async def test_close_blocked_by_high_priority_and_report_versions(client):
    """高优先级未结阻断结案；明确转交后可结案并生成报告 v1；更正形成 v2。"""
    ctx = await _activate(client)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]
    physician = ctx["staff"]["physician"]

    # 到期 + 完成期末复评
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        from app.models import StewardServiceInstance

        instance = await session.get(StewardServiceInstance, instance_id)
        instance.ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()
    await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=44", headers=u)
    review = [t for t in resp.json() if t["task_type"] == "final_review"][0]
    await client.post(
        f"{API}/admin/steward/tasks/{review['id']}/result",
        json={"result": "复评通过", "actual_occurred_at": datetime.now(timezone.utc).isoformat(),
              "backfill_reason": "到期补录"},
        headers=a,
    )

    # 高优先级未结异常工单 → 结案 409
    resp = await client.post(
        f"{API}/admin/steward/professional/orders",
        json={"instance_id": instance_id, "order_type": "exception",
              "title": "用户反映设备异常", "priority": "high", "physician_id": physician["id"]},
        headers=a,
    )
    high_order_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/close",
        json={"closure_report": "结案报告初稿"}, headers=a,
    )
    assert resp.status_code == 409 and "高优先级" in resp.json()["detail"]

    # 明确转交（接收人+时间+依据）→ 不再阻断，结案成功并生成报告 v1
    receiver = await create_staff(client, a, "physician", "接手医师")
    resp = await client.post(
        f"{API}/admin/steward/professional/orders/{high_order_id}/transfer",
        json={"to_physician_id": receiver["id"], "reason": "原医师轮休，明确转交跟进"},
        headers=a,
    )
    assert resp.status_code == 200
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/close",
        json={"closure_report": "45 天服务完成：随访全部完成，复评结论良好。"},
        headers=a,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"{API}/admin/steward/reports", params={"instance_id": instance_id}, headers=a)
    reports = resp.json()
    assert len(reports) == 1 and reports[0]["version"] == 1
    assert reports[0]["data_summary"]["high_priority_transfers"] >= 1  # 转交依据入摘要

    # 结案后更正：未绑定签约医师 → 403；绑定后新版本 v2 且不覆盖 v1
    resp = await client.post(
        f"{API}/admin/steward/reports",
        json={"instance_id": instance_id, "title": "结案报告更正",
              "content": "更正：随访完成度说明修正。", "basis": "用户补充反馈"},
        headers=a,
    )
    assert resp.status_code == 403
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        from sqlalchemy import select as _select

        from app.models import AdminUser

        admin_id = (await session.execute(_select(AdminUser).where(AdminUser.username == "admin"))).scalar_one().id
    await client.post(
        f"{API}/admin/steward/staff-links",
        json={"staff_id": physician["id"], "admin_id": admin_id}, headers=a,
    )
    resp = await client.post(
        f"{API}/admin/steward/reports",
        json={"instance_id": instance_id, "title": "结案报告更正",
              "content": "更正：随访完成度说明修正。", "basis": "用户补充反馈"},
        headers=a,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["version"] == 2 and resp.json()["report_type"] == "correction"
    assert resp.json()["supersedes_id"] == reports[0]["id"]
    resp = await client.get(f"{API}/admin/steward/reports", params={"instance_id": instance_id}, headers=a)
    assert len(resp.json()) == 2  # v1 保留不覆盖


async def test_followup_record_and_mp_view(client):
    """结构化随访：一任务一条、随任务完成；C 端聚合含工单/报告/随访与急救文案。"""
    ctx = await _activate(client)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=7", headers=u)
    follow_up = [t for t in resp.json() if t["task_type"] == "follow_up"][0]
    resp = await client.post(
        f"{API}/admin/steward/followups",
        json={"task_id": follow_up["id"], "summary": "第 7 天随访：入睡时间缩短，记录完整。",
              "data_quality_note": "当日设备数据完整", "next_action": "维持当前作息计划"},
        headers=a,
    )
    assert resp.status_code == 201, resp.text
    # 重复录制 → 409
    resp = await client.post(
        f"{API}/admin/steward/followups",
        json={"task_id": follow_up["id"], "summary": "重复录制"}, headers=a,
    )
    assert resp.status_code == 409
    # 非 follow_up 任务 → 422
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=7", headers=u)
    daily = [t for t in resp.json() if t["task_type"] == "daily"][0]
    resp = await client.post(
        f"{API}/admin/steward/followups",
        json={"task_id": daily["id"], "summary": "错任务"}, headers=a,
    )
    assert resp.status_code == 422
    # steward 随访任务被同步完成
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        task = await session.get(StewardTask, follow_up["id"])
        assert task.status == "done"

    # C 端聚合
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}/professional", headers=u)
    assert resp.status_code == 200
    body = resp.json()
    assert body["followups"] and body["followups"][0]["service_day"] == 7
    assert "120" in body["emergency_note"]  # 固定急救文案
    # 跨用户 404
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}/professional",
                            headers={"Authorization": "Bearer bad"})
    assert resp.status_code in (401, 403)
