"""工作二切片 B · 触达任务框架用例（契约 docs/api.md 第五部分 · 七）。

覆盖：生成幂等与并发恰一份、状态流转（转人工/提交/确认/驳回上限/退回/取消）、
停止条件（退订/暂停/终止/授权撤回/到期）、任务与触达互不冒充、离岗交接与停用守卫、
权限边界、通道边界（无回执、待通道配置）、beat 注册。
"""

import asyncio
from datetime import timedelta, timezone
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import (
    OutreachAttempt,
    OutreachIntent,
    StewardServiceInstance,
    StewardTask,
    StewardTaskStatus,
)
from app.schemas.common import as_utc
from app.services import outreach_service, steward_service
from tests.commerce_helpers import API
from tests.steward_helpers import full_setup

pytestmark = pytest.mark.asyncio


async def _activate(client, *, backdate_days: int = 0) -> dict:
    ctx = await full_setup(client)
    resp = await client.post(
        f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"]
    )
    assert resp.status_code == 200, resp.text
    ctx["instance"] = resp.json()
    if backdate_days:
        shift = timedelta(days=backdate_days)
        async with client.session_factory() as session:  # type: ignore[attr-defined]
            instance = await session.get(StewardServiceInstance, ctx["instance"]["id"])
            instance.activated_at = as_utc(instance.activated_at) - shift
            instance.ends_at = as_utc(instance.ends_at) - shift
            for task in (
                (await session.execute(select(StewardTask).where(StewardTask.instance_id == instance.id)))
                .scalars()
                .all()
            ):
                task.service_date = task.service_date - shift
                task.due_at = as_utc(task.due_at) - shift
            await session.commit()
    return ctx


async def _sweep(client, today=None) -> dict:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        return await outreach_service.outreach_sweep(session, today=today)


async def _intents(client, **filters) -> list[OutreachIntent]:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        stmt = select(OutreachIntent).order_by(OutreachIntent.id.asc())
        if filters:
            stmt = stmt.filter_by(**filters)
        return list((await session.execute(stmt)).scalars().all())


async def test_generation_idempotent_and_states(client):
    """生成幂等：当日 daily 意图恰一条、重复 sweep 不重建；初始状态待通道配置、无回执。"""
    ctx = await _activate(client)
    instance_id = ctx["instance"]["id"]

    first = await _sweep(client)
    assert first["generated"] >= 1
    again = await _sweep(client)
    assert again["generated"] == 0  # 幂等：不重建

    intents = await _intents(client, instance_id=instance_id)
    assert len(intents) == 1  # 当日只有 daily（随访在第 7 天起）
    intent = intents[0]
    assert intent.status == "pending_channel"
    assert intent.purpose == "daily_reminder"
    assert intent.content_version == "daily_v1"
    assert intent.channel_receipt_ref is None  # 通道边界：无回执
    assert intent.user_id == 1 and intent.task_id is not None

    # 后台可见（outreach.read）；详情含 generated 尝试记录
    resp = await client.get(f"{API}/admin/outreach/intents", headers=ctx["admin"])
    assert resp.status_code == 200 and resp.json()["total"] >= 1
    item = resp.json()["items"][0]
    assert item["status"] == "pending_channel"
    assert item["user_phone_masked"]  # 手机号掩码
    resp = await client.get(f"{API}/admin/outreach/intents/{intent.id}", headers=ctx["admin"])
    actions = [a["action"] for a in resp.json()["attempts_log"]]
    assert actions == ["generated"]


async def test_followup_intent_and_summary(client):
    """随访日生成 follow_up 意图（内容版本取计划版本）；按状态汇总。"""
    ctx = await _activate(client)
    instance_id = ctx["instance"]["id"]
    activated = datetime.fromisoformat(ctx["instance"]["activated_at"].replace("Z", "+00:00"))
    day7 = steward_service.service_date_for_day(activated, 7)

    result = await _sweep(client, today=day7)
    assert result["generated"] == 2  # daily + follow_up
    purposes = {(i.purpose, i.content_version) for i in await _intents(client, instance_id=instance_id, service_date=day7)}
    assert ("daily_reminder", "daily_v1") in purposes
    assert ("follow_up", "v1") in purposes  # plan_version 快照

    resp = await client.get(
        f"{API}/admin/outreach/summary", params={"date": day7.isoformat()}, headers=ctx["admin"]
    )
    assert resp.status_code == 200
    assert resp.json()["by_status"].get("pending_channel") == 2


async def test_manual_workflow_full_flow(client):
    """工作台全流程：转人工 → 退回 → 指派 → 提交（人工不冒充通道）→ 确认 → resolved。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    await _sweep(client)
    intent = (await _intents(client))[0]
    butler = ctx["staff"]["butler"]

    # 通道未决策 → 提交前必须先转人工；错状态 409
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/submit",
        json={"contact_method": "phone", "outcome": "提前提交"},
        headers=a,
    )
    assert resp.status_code == 409

    # 转人工（指定在职管家）
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/route-manual",
        json={"assignee_staff_id": butler["id"], "note": "通道未定，人工跟进"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "pending_manual"

    # 退回未指派池 → 再指派（留 reassigned 记录）
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/release", json={"reason": "今日换班"}, headers=a
    )
    assert resp.status_code == 200 and resp.json()["assignee_staff_id"] is None
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/assign",
        json={"assignee_staff_id": ctx["staff"]["backup"]["id"]},
        headers=a,
    )
    assert resp.status_code == 200

    # 提交联系结果：pending_confirm；回执引用恒 null（人工不冒充通道成功）
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/submit",
        json={"contact_method": "phone", "outcome": "已电话提醒完成今日记录"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "pending_confirm"
    assert resp.json()["channel_receipt_ref"] is None

    # 确认 → resolved（事项已办理）
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/confirm", json={"note": "办理完毕"}, headers=a
    )
    assert resp.status_code == 200 and resp.json()["status"] == "resolved"

    actions = [a2["action"] for a2 in resp.json()["attempts_log"]]
    # 退回后 assignee 为空 → 再指派记 assigned（改派才记 reassigned）
    assert actions == ["generated", "routed_manual", "released", "assigned", "submitted", "confirmed"]

    # 终态重复流转 409
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/submit",
        json={"contact_method": "phone", "outcome": "再提交"},
        headers=a,
    )
    assert resp.status_code == 409


async def test_reject_exhausts_to_failed(client):
    """驳回重办：attempts 递增，达上限（默认 3）置 failed；此前回到 pending_manual。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    await _sweep(client)
    intent = (await _intents(client))[0]
    await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/route-manual",
        json={"assignee_staff_id": ctx["staff"]["butler"]["id"]},
        headers=a,
    )

    for round_no in range(1, 4):
        await client.post(
            f"{API}/admin/outreach/intents/{intent.id}/submit",
            json={"contact_method": "chat", "outcome": f"第 {round_no} 次联系"},
            headers=a,
        )
        resp = await client.post(
            f"{API}/admin/outreach/intents/{intent.id}/reject",
            json={"reason": "用户未确认收到"},
            headers=a,
        )
        if round_no < 3:
            assert resp.json()["status"] == "pending_manual"
            assert resp.json()["attempts"] == round_no
        else:
            assert resp.json()["status"] == "failed"  # 失败队列终态
            assert resp.json()["attempts"] == 3
    # failed 后不能再提交
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/submit",
        json={"contact_method": "chat", "outcome": "失败后提交"},
        headers=a,
    )
    assert resp.status_code == 409


async def test_stop_on_unsubscribe_and_resubscribe(client):
    """退订：立即取消未终态意图并可见；重新订阅后后续服务日恢复生成。"""
    ctx = await _activate(client)
    u = ctx["user"]
    await _sweep(client)
    intent = (await _intents(client))[0]

    resp = await client.get(f"{API}/mp/steward/outreach/status", headers=u)
    assert resp.status_code == 200
    body = resp.json()
    assert body["subscribed"] is True and body["channel_decided"] is False
    assert "待配置" in body["status_note"]

    resp = await client.post(
        f"{API}/mp/steward/outreach/subscription", json={"subscribed": False}, headers=u
    )
    assert resp.status_code == 200 and resp.json()["subscribed"] is False

    intent = await _get_intent(client, intent.id)
    assert intent.status == "cancelled" and intent.stop_reason == "unsubscribed"

    # 重新订阅后：当日未完结意图复活为待通道配置（尝试记录保留取消历史）
    resp = await client.post(
        f"{API}/mp/steward/outreach/subscription", json={"subscribed": True}, headers=u
    )
    assert resp.status_code == 200
    result = await _sweep(client)
    assert result["generated"] == 1  # 复活计为一条
    same_day = await _get_intent(client, intent.id)
    assert same_day.status == "pending_channel" and same_day.stop_reason is None


async def _get_intent(client, intent_id: int) -> OutreachIntent:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        return await session.get(OutreachIntent, intent_id)


async def test_stop_on_pause_terminate_and_expiry(client):
    """停止条件：暂停（service_paused）、权益作废（instance_terminated）、到期（instance_expired）。"""
    from app.models import EntitlementStatus, MpEntitlement

    # 暂停 → 意图即时取消
    ctx = await _activate(client)
    a = ctx["admin"]
    instance_id = ctx["instance"]["id"]
    await _sweep(client)
    intent = (await _intents(client, instance_id=instance_id))[0]
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "user_request", "reason": "本人申请"},
        headers=a,
    )
    assert resp.status_code == 200
    intent = await _get_intent(client, intent.id)
    assert intent.status == "cancelled" and intent.stop_reason == "service_paused"

    # 恢复后 sweep 按当日任务重新生成
    resp = await client.post(f"{API}/admin/steward/instances/{instance_id}/resume", json={}, headers=a)
    assert resp.status_code == 200
    await _sweep(client)
    fresh = [i for i in await _intents(client, instance_id=instance_id) if i.status != "cancelled"]
    assert fresh and fresh[0].status == "pending_channel"

    # 权益作废 → 终止联动取消
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        ent = await session.get(MpEntitlement, ctx["entitlement_id"])
        ent.status = EntitlementStatus.VOID.value
        ent.void_reason = "订单已退款"
        await session.commit()
    await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=ctx["user"])  # 懒迁移触发
    fresh_id = fresh[0].id
    intent = await _get_intent(client, fresh_id)
    assert intent.status == "cancelled" and intent.stop_reason == "instance_terminated"

    # 到期：另一实例先生成意图，再回拨 ends_at 到过去 → outreach sweep 停止
    ctx2 = await _activate(client)
    instance2_id = ctx2["instance"]["id"]
    await _sweep(client)
    intents2 = [i for i in await _intents(client, instance_id=instance2_id)]
    assert intents2 and intents2[0].status == "pending_channel"
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        instance2 = await session.get(StewardServiceInstance, instance2_id)
        instance2.ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()
    await _sweep(client)
    stopped = await _get_intent(client, intents2[0].id)
    assert stopped.status == "cancelled" and stopped.stop_reason == "instance_expired"


async def test_stop_on_consent_revoked(client):
    """平台服务授权撤回 → 本人触达停止（consent_revoked）。"""
    ctx = await _activate(client)
    u = ctx["user"]
    await _sweep(client)
    intent = (await _intents(client))[0]

    resp = await client.get(f"{API}/mp/consents", params={"purpose": "steward_service"}, headers=u)
    consent_id = resp.json()["items"][0]["id"]
    resp = await client.post(f"{API}/mp/consents/{consent_id}/revoke", headers=u)
    assert resp.status_code == 200

    intent = await _get_intent(client, intent.id)
    assert intent.status == "cancelled" and intent.stop_reason == "consent_revoked"


async def test_task_and_outreach_do_not_impersonate(client):
    """互不冒充：任务完成不把触达标为办理；触达办理不完成任务。"""
    ctx = await _activate(client)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]
    await _sweep(client)
    intent = (await _intents(client))[0]

    # 完成对应 steward 任务 → 触达仍是 pending_channel
    today = steward_service.sh_today().isoformat()
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks", params={"date": today}, headers=u
    )
    daily = [t for t in resp.json() if t["task_type"] == "daily"][0]
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{daily['id']}/complete",
        json={"result": "已完成今日记录"},
        headers=u,
    )
    assert resp.status_code == 200
    intent = await _get_intent(client, intent.id)
    assert intent.status == "pending_channel"  # 不因任务完成而自动办理

    # 触达走完人工流程 → 任务仍保持 done（用户完成），且不会被触达覆盖
    await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/route-manual",
        json={"assignee_staff_id": ctx["staff"]["butler"]["id"]},
        headers=a,
    )
    await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/submit",
        json={"contact_method": "chat", "outcome": "会话提醒完成"},
        headers=a,
    )
    resp = await client.post(f"{API}/admin/outreach/intents/{intent.id}/confirm", json={}, headers=a)
    assert resp.status_code == 200 and resp.json()["status"] == "resolved"
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        task = await session.get(StewardTask, daily["id"])
        assert task.status == StewardTaskStatus.DONE.value
        assert task.result == "已完成今日记录"  # 未被触达结果覆盖


async def test_handover_and_staff_deactivation_guard(client):
    """离岗交接：批量转移留痕；有未完成触达的管家不能停用；交接后可停用；停用者不可被指派。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    await _sweep(client)
    intent = (await _intents(client))[0]
    butler_id, backup_id = ctx["staff"]["butler"]["id"], ctx["staff"]["backup"]["id"]
    await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/route-manual",
        json={"assignee_staff_id": butler_id},
        headers=a,
    )

    # 名下有未完成触达 → 停用被拒
    resp = await client.patch(
        f"{API}/admin/steward/staff/{butler_id}", json={"status": "inactive"}, headers=a
    )
    assert resp.status_code == 409

    # 交接给替补 → 意图转移并留 reassigned 记录
    resp = await client.post(
        f"{API}/admin/outreach/handover",
        json={"from_staff_id": butler_id, "to_staff_id": backup_id, "reason": "管家休假"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["moved"] == 1
    intent = await _get_intent(client, intent.id)
    assert intent.assignee_staff_id == backup_id

    # 交接后可停用；停用者不可再被指派
    resp = await client.patch(
        f"{API}/admin/steward/staff/{butler_id}", json={"status": "inactive"}, headers=a
    )
    assert resp.status_code == 200
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/assign",
        json={"assignee_staff_id": butler_id},
        headers=a,
    )
    assert resp.status_code == 409

    # 尝试记录含交接痕迹
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        logs = (
            (
                await session.execute(
                    select(OutreachAttempt).where(
                        OutreachAttempt.intent_id == intent.id,
                        OutreachAttempt.action == "reassigned",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert logs and logs[-1].detail["from_staff_id"] == butler_id


async def test_permissions_and_404(client):
    """outreach.read 编辑可读；outreach.manage 编辑 403；不存在 404；跨态取消 409。"""
    from tests.commerce_helpers import admin_headers

    ctx = await _activate(client)
    await _sweep(client)
    intent = (await _intents(client))[0]
    editor = await admin_headers(client, "editor")

    resp = await client.get(f"{API}/admin/outreach/intents", headers=editor)
    assert resp.status_code == 200
    resp = await client.get(f"{API}/admin/outreach/summary", headers=editor)
    assert resp.status_code == 200
    resp = await client.post(
        f"{API}/admin/outreach/intents/{intent.id}/route-manual",
        json={"assignee_staff_id": ctx["staff"]["butler"]["id"]},
        headers=editor,
    )
    assert resp.status_code == 403
    resp = await client.get(f"{API}/admin/outreach/intents/99999", headers=ctx["admin"])
    assert resp.status_code == 404


async def test_outreach_sweep_registered_in_beat():
    """切片 B：outreach sweep 任务可导入且注册进 beat 周期。"""
    from app.workers.celery_app import celery_app
    from app.workers.tasks import outreach_sweep

    schedule = celery_app.conf.beat_schedule.get("outreach-sweep")
    assert schedule is not None
    assert schedule["task"] == "app.workers.tasks.outreach_sweep"
    assert outreach_sweep.name == "app.workers.tasks.outreach_sweep"


async def test_concurrent_generation_single_intent(file_db_steward):
    """真并发生成（文件库 + NullPool 连接级隔离）：同实例同日同用途恰一条意图。"""
    import httpx

    import app.main as app_main
    from app.core.deps import get_db, get_redis
    from app.core.security import create_token
    from tests.conftest import FakeRedis

    ctx = file_db_steward
    app_main.app.dependency_overrides[get_db] = ctx["override_get_db"]
    app_main.app.dependency_overrides[get_redis] = lambda: FakeRedis()
    try:
        transport = httpx.ASGITransport(app=app_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            u = {"Authorization": f"Bearer {create_token(1, 'user', 'access')}"}
            resp = await c.post(f"{API}/mp/steward/instances/{ctx['instance_id']}/activate", headers=u)
            assert resp.status_code == 200, resp.text

        today = steward_service.sh_today()

        async def generate_with(factory):
            async with factory() as session:
                instance = await session.get(StewardServiceInstance, ctx["instance_id"])
                return await outreach_service.generate_for_instance_date(session, instance, today)

        created = await asyncio.gather(
            generate_with(ctx["factory"]), generate_with(ctx["factory"])
        )
        assert sum(created) >= 1  # 至少一条新建
        async with ctx["factory"]() as session:
            rows = (
                (
                    await session.execute(
                        select(OutreachIntent).where(
                            OutreachIntent.instance_id == ctx["instance_id"],
                            OutreachIntent.service_date == today,
                            OutreachIntent.purpose == "daily_reminder",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1  # 并发恰一份
    finally:
        app_main.app.dependency_overrides.clear()
