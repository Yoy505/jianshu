"""工作二 · 生命周期扫描与任务门禁用例（A3）。

覆盖：无人读页面时 sweep 推进到期 / 权益作废终止（幂等）、beat 注册、
本人任务门禁（暂停 / 终止 / 未来 / 补录）、后台登记补录字段、结案工单钩子接线。
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models import (
    EntitlementStatus,
    MpEntitlement,
    StewardServiceInstance,
    StewardTask,
    StewardTimelineEvent,
)
from tests.commerce_helpers import API
from tests.steward_helpers import full_setup

pytestmark = pytest.mark.asyncio


async def _activate(client) -> dict:
    ctx = await full_setup(client)
    resp = await client.post(
        f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"]
    )
    assert resp.status_code == 200, resp.text
    ctx["instance"] = resp.json()
    return ctx


async def test_sweep_expires_without_page_read(client):
    """到期实例无人访问页面：sweep 直接推进 expired + 时间线；重复 sweep 幂等。"""
    from app.services import steward_service as svc

    ctx = await _activate(client)
    instance_id = ctx["instance"]["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        instance = await session.get(StewardServiceInstance, instance_id)
        instance.ends_at = datetime.now(timezone.utc) - timedelta(hours=2)
        await session.commit()

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.lifecycle_sweep(session)
    assert result["expired"] == 1

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        instance = await session.get(StewardServiceInstance, instance_id)
        assert instance.status == "expired"
        events = (
            (
                await session.execute(
                    select(StewardTimelineEvent.event_type).where(
                        StewardTimelineEvent.instance_id == instance_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "expired" in events

    # 幂等：第二次 sweep 无可推进项
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.lifecycle_sweep(session)
    assert result == {"expired": 0, "terminated": 0}


async def test_sweep_terminates_voided_entitlement(client):
    """权益作废后无人访问页面：sweep 终止实例并取消待办任务。"""
    from app.services import steward_service as svc

    ctx = await _activate(client)
    instance_id = ctx["instance"]["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        ent = await session.get(MpEntitlement, ctx["entitlement_id"])
        ent.status = EntitlementStatus.VOID.value
        ent.void_reason = "订单已退款"
        ent.voided_at = datetime.now(timezone.utc)
        await session.commit()

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        result = await svc.lifecycle_sweep(session)
    assert result["terminated"] == 1

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        instance = await session.get(StewardServiceInstance, instance_id)
        assert instance.status == "terminated"
        assert instance.termination_reason == "订单已退款"
        statuses = (
            (
                await session.execute(
                    select(StewardTask.status).where(StewardTask.instance_id == instance_id)
                )
            )
            .scalars()
            .all()
        )
        assert "pending" not in statuses


async def test_sweep_task_registered_in_beat():
    """A3：sweep 任务可导入且已注册进 beat 周期（每 5 分钟）。"""
    from app.workers.celery_app import celery_app
    from app.workers.tasks import steward_lifecycle_sweep

    schedule = celery_app.conf.beat_schedule.get("steward-lifecycle-sweep")
    assert schedule is not None
    assert schedule["task"] == "app.workers.tasks.steward_lifecycle_sweep"
    assert steward_lifecycle_sweep.name == "app.workers.tasks.steward_lifecycle_sweep"


async def test_user_task_gates(client):
    """A3 任务门禁：暂停 409、终止 409、未来任务 409、历史补录须填原因。"""
    ctx = await _activate(client)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]

    # 未来任务（第 40 天）不能提前完成
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=40", headers=u
    )
    future_daily = [t for t in resp.json() if t["task_type"] == "daily"][0]
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{future_daily['id']}/complete",
        json={"result": "提前打卡"},
        headers=u,
    )
    assert resp.status_code == 409

    # 暂停中不能完成任务
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "user_request", "reason": "本人申请暂停"},
        headers=a,
    )
    assert resp.status_code == 200
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks", params={"date": today.isoformat()}, headers=u
    )
    today_daily = [t for t in resp.json() if t["task_type"] == "daily"]
    if today_daily:
        resp = await client.post(
            f"{API}/mp/steward/instances/{instance_id}/tasks/{today_daily[0]['id']}/complete",
            json={"result": "暂停中打卡"},
            headers=u,
        )
        assert resp.status_code == 409
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/resume", json={}, headers=a
    )
    assert resp.status_code == 200

    # 历史日期（激活日之前不存在）——构造：把第 1 天任务日期改到昨天，补录须填原因
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        task = (
            (
                await session.execute(
                    select(StewardTask).where(
                        StewardTask.instance_id == instance_id,
                        StewardTask.task_type == "daily",
                        StewardTask.service_day == 1,
                    )
                )
            )
            .scalars()
            .one()
        )
        task.service_date = today - timedelta(days=1)
        task_id = task.id
        await session.commit()

    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{task_id}/complete",
        json={"result": "昨天忘了记录"},
        headers=u,
    )
    assert resp.status_code == 422  # 补录原因必填
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{task_id}/complete",
        json={"result": "补记昨日执行情况", "backfill_reason": "昨日出差未及时记录"},
        headers=u,
    )
    assert resp.status_code == 200
    assert resp.json()["backfill_reason"]

    # 终止后不能完成任务
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        ent = await session.get(MpEntitlement, ctx["entitlement_id"])
        ent.status = EntitlementStatus.VOID.value
        ent.void_reason = "退款"
        await session.commit()
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)  # 触发懒迁移
    assert resp.json()["status"] == "terminated"
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=2", headers=u
    )
    daily2 = [t for t in resp.json() if t["task_type"] == "daily"][0]
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{daily2['id']}/complete",
        json={"result": "终止后打卡"},
        headers=u,
    )
    assert resp.status_code == 409


async def test_admin_future_result_requires_backfill(client):
    """A3：后台登记未来日期任务，无补录字段 422、带字段 200 且留痕。"""
    ctx = await _activate(client)
    a = ctx["admin"]
    instance_id = ctx["instance"]["id"]

    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=14", headers=ctx["user"]
    )
    follow_up = [t for t in resp.json() if t["task_type"] == "follow_up"][0]

    resp = await client.post(
        f"{API}/admin/steward/tasks/{follow_up['id']}/result",
        json={"result": "第 14 天随访完成"},
        headers=a,
    )
    assert resp.status_code == 422

    occurred = datetime.now(timezone.utc).isoformat()
    resp = await client.post(
        f"{API}/admin/steward/tasks/{follow_up['id']}/result",
        json={
            "result": "第 14 天随访完成，睡眠评分提升",
            "actual_occurred_at": occurred,
            "backfill_reason": "随访完成后次日统一录入",
        },
        headers=a,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done"
    assert body["backfill_reason"]
    assert body["actual_occurred_at"]

    # 重复登记不覆盖原结果
    resp = await client.post(
        f"{API}/admin/steward/tasks/{follow_up['id']}/result",
        json={"result": "试图覆盖", "actual_occurred_at": occurred, "backfill_reason": "再次补录"},
        headers=a,
    )
    assert resp.status_code == 409


async def test_close_checks_high_priority_hook(client):
    """A3：结案的高优先级工单检查已接线（D 落地前钩子返回 0，结案可达）。"""
    from app.services import steward_service as svc

    ctx = await _activate(client)
    a = ctx["admin"]
    instance_id = ctx["instance"]["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        instance = await session.get(StewardServiceInstance, instance_id)
        instance.ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=ctx["user"])
    assert resp.json()["status"] == "expired"
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        assert await svc._open_high_priority_tickets(session, instance_id) == 0  # D 前占位为 0

    # 完成复评（补录字段）后结案成功
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=44", headers=ctx["user"]
    )
    review = [t for t in resp.json() if t["task_type"] == "final_review"][0]
    resp = await client.post(
        f"{API}/admin/steward/tasks/{review['id']}/result",
        json={
            "result": "复评结论良好",
            "actual_occurred_at": datetime.now(timezone.utc).isoformat(),
            "backfill_reason": "到期补录",
        },
        headers=a,
    )
    assert resp.status_code == 200
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/close",
        json={"closure_report": "结案归档"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "closed"
