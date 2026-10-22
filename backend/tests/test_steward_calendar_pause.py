"""工作二 · 45 天日历、暂停补时、到期 / 退款联动、结案与续期用例。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models import (
    EntitlementStatus,
    MpEntitlement,
    StewardPauseRecord,
    StewardServiceInstance,
    StewardTask,
    StewardTimelineEvent,
)
from app.schemas.common import as_utc
from tests.commerce_helpers import API, admin_headers
from tests.steward_helpers import full_setup

pytestmark = pytest.mark.asyncio
SH = ZoneInfo("Asia/Shanghai")


async def activate(client, steward_env) -> dict:
    ctx = await full_setup(client)
    resp = await client.post(
        f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"]
    )
    assert resp.status_code == 200, resp.text
    ctx["instance"] = resp.json()
    return ctx


async def test_calendar_nodes_and_dates(client, steward_env):
    """45 天日历：每日事项、6 次随访、43/44/45 复评结案节点与日期映射正确。"""
    ctx = await activate(client, steward_env)
    u = ctx["user"]
    instance_id = ctx["instance"]["id"]
    activated = ctx["instance"]["activated_at"]
    base = datetime.fromisoformat(activated.replace("Z", "+00:00")).astimezone(SH).date()

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=7", headers=u)
    day7 = resp.json()
    assert {t["task_type"] for t in day7} == {"daily", "follow_up"}

    for day, task_type in [
        (7, "follow_up"), (14, "follow_up"), (21, "follow_up"),
        (28, "follow_up"), (35, "follow_up"), (42, "follow_up"),
        (43, "review_prep"), (44, "final_review"), (45, "closure"),
    ]:
        resp = await client.get(
            f"{API}/mp/steward/instances/{instance_id}/tasks?service_day={day}", headers=u
        )
        types = {t["task_type"] for t in resp.json()}
        assert task_type in types, f"第 {day} 天缺少 {task_type}：{types}"
        target = [t for t in resp.json() if t["task_type"] == task_type][0]
        assert target["service_date"] == (base + timedelta(days=day - 1)).isoformat()

    # 任务总量：45 daily + 6 follow_up + 3 复评结案
    counts = {"daily": 0, "follow_up": 0, "review_prep": 0, "final_review": 0, "closure": 0}
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        for task in (
            (await session.execute(select(StewardTask).where(StewardTask.instance_id == instance_id)))
            .scalars()
            .all()
        ):
            counts[task.task_type] += 1
    assert counts == {"daily": 45, "follow_up": 6, "review_prep": 1, "final_review": 1, "closure": 1}

    # 月视图：激活当月第 1 天对应业务日 1；跨月查询同样可命中
    month = base.strftime("%Y-%m")
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/calendar", params={"month": month}, headers=u
    )
    days = {d["date"]: d for d in resp.json()}
    day1 = days[base.isoformat()]
    assert day1["service_day"] == 1
    assert any(t["task_type"] == "daily" for t in day1["tasks"])
    next_month = (base.replace(day=28) + timedelta(days=5)).strftime("%Y-%m")
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/calendar", params={"month": next_month}, headers=u
    )
    assert any(d["tasks"] for d in resp.json())


async def test_user_daily_task_complete_flow(client, steward_env):
    """本人完成自己的每日任务；管家任务拒绝、重复完成 409。"""
    ctx = await activate(client, steward_env)
    u = ctx["user"]
    instance_id = ctx["instance"]["id"]
    today = datetime.now(SH).date().isoformat()
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks", params={"date": today}, headers=u
    )
    tasks = {t["task_type"]: t for t in resp.json()}
    assert "daily" in tasks

    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{tasks['daily']['id']}/complete",
        json={"result": "今日睡眠记录 7 小时，晨起精神良好"},
        headers=u,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "done"

    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{tasks['daily']['id']}/complete",
        json={"result": "重复提交"},
        headers=u,
    )
    assert resp.status_code == 409

    if "follow_up" in tasks:  # 当天恰逢随访节点时验证服务方任务拦截
        resp = await client.post(
            f"{API}/mp/steward/instances/{instance_id}/tasks/{tasks['follow_up']['id']}/complete",
            json={"result": "越权"},
            headers=u,
        )
        assert resp.status_code == 403


async def _backdate_pause(client, hours: float) -> int:
    """把进行中的暂停记录 started_at 回拨指定小时，模拟真实中断时长。"""
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        record = (
            (
                await session.execute(
                    select(StewardPauseRecord).where(StewardPauseRecord.ended_at.is_(None))
                )
            )
            .scalars()
            .one()
        )
        record.started_at = datetime.now(timezone.utc) - timedelta(hours=hours)
        await session.commit()
        return record.id


async def test_pause_over_24h_compensates_and_shifts(client, steward_env):
    """服务方核心中断 >24h：全额补时，ends_at 顺延，未开始任务按天平移留痕。"""
    ctx = await activate(client, steward_env)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]
    ends_before = datetime.fromisoformat(ctx["instance"]["ends_at"].replace("Z", "+00:00"))

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "provider_interruption", "reason": "设备云接口故障，核心数据中断"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "paused"
    pause_id = await _backdate_pause(client, hours=25)

    # 暂停期间用户读取正常（暂停态）
    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    assert resp.status_code == 200 and resp.json()["status"] == "paused"

    future_day = 40
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks?service_day={future_day}", headers=u
    )
    daily_40 = [t for t in resp.json() if t["task_type"] == "daily"][0]
    date_before = daily_40["service_date"]

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/resume", json={"note": "故障恢复"}, headers=a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["compensated_seconds"] >= 25 * 3600 - 60

    ends_after = datetime.fromisoformat(body["ends_at"].replace("Z", "+00:00"))
    extension = ends_after - ends_before
    assert timedelta(hours=24.9) < extension < timedelta(hours=25.1)

    # 未开始任务平移 ceil(25h)=2 天，留痕 shifted_days
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks?service_day={future_day}", headers=u
    )
    daily_40_after = [t for t in resp.json() if t["task_type"] == "daily"][0]
    from datetime import date

    assert date.fromisoformat(daily_40_after["service_date"]) == date.fromisoformat(date_before) + timedelta(days=2)
    assert daily_40_after["shifted_days"] == 2

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        record = await session.get(StewardPauseRecord, pause_id)
        assert record.compensated_seconds == record.duration_seconds
        assert record.tasks_shifted_days == 2
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
        assert "paused" in events and "resumed" in events and "compensated" in events


async def test_pause_under_24h_records_without_compensation(client, steward_env):
    """<24h 服务方中断：只记录，不补时、不平移。"""
    ctx = await activate(client, steward_env)
    a = ctx["admin"]
    instance_id = ctx["instance"]["id"]
    ends_before = ctx["instance"]["ends_at"]

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "provider_interruption", "reason": "短暂故障 10 小时"},
        headers=a,
    )
    assert resp.status_code == 200
    await _backdate_pause(client, hours=10)
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/resume", json={}, headers=a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["compensated_seconds"] == 0
    assert body["ends_at"] == ends_before


async def test_physician_advised_pause_full_compensation(client, steward_env):
    """医师建议暂停：无论时长全额补时。"""
    ctx = await activate(client, steward_env)
    a = ctx["admin"]
    instance_id = ctx["instance"]["id"]
    ends_before = datetime.fromisoformat(ctx["instance"]["ends_at"].replace("Z", "+00:00"))

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "physician_advised", "reason": "医师建议住院观察期间暂停居家计划"},
        headers=a,
    )
    assert resp.status_code == 200
    await _backdate_pause(client, hours=6)
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/resume", json={"note": "医嘱恢复"}, headers=a
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["compensated_seconds"] >= 6 * 3600 - 60
    ends_after = datetime.fromisoformat(body["ends_at"].replace("Z", "+00:00"))
    assert ends_after - ends_before >= timedelta(hours=5.9)


async def test_expiry_and_close_flow(client, steward_env):
    """到期懒迁移 expired；结案要求期末复评完成与结案报告；结案后 closed。"""
    ctx = await activate(client, steward_env)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        instance = await session.get(StewardServiceInstance, instance_id)
        instance.ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        instance.original_ends_at = instance.ends_at
        await session.commit()

    # 未复评先结案 → 409
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/close",
        json={"closure_report": "服务完成，报告归档。"},
        headers=a,
    )
    assert resp.status_code == 409

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    assert resp.status_code == 200
    assert resp.json()["status"] == "expired"  # 读路径懒迁移

    # 完成第 44 天期末复评任务（后台登记结果）
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=44", headers=u
    )
    review = [t for t in resp.json() if t["task_type"] == "final_review"][0]
    resp = await client.post(
        f"{API}/admin/steward/tasks/{review['id']}/result",
        json={"result": "期末复评：睡眠效率提升，周期结束后建议维持当前作息。"},
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "done"

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/close",
        json={"closure_report": "45 天服务完成：6 次随访全部完成，期末复评结论良好，结案归档。"},
        headers=a,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "closed"
    assert resp.json()["closed_at"] is not None


async def test_refund_terminates_instance(client, steward_env):
    """权益作废（退款联动）→ 实例 terminated、未完成任务取消、激活 409。"""
    ctx = await activate(client, steward_env)
    u = ctx["user"]
    instance_id = ctx["instance"]["id"]

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        ent = await session.get(MpEntitlement, ctx["entitlement_id"])
        ent.status = EntitlementStatus.VOID.value
        ent.void_reason = "订单已退款"
        ent.voided_at = datetime.now(timezone.utc)
        await session.commit()

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    assert resp.json()["status"] == "terminated"
    assert resp.json()["termination_reason"] == "订单已退款"

    resp = await client.post(f"{API}/mp/steward/instances/{instance_id}/activate", headers=u)
    assert resp.status_code == 409

    async with client.session_factory() as session:  # type: ignore[attr-defined]
        pending = (
            (
                await session.execute(
                    select(StewardTask.status).where(StewardTask.instance_id == instance_id)
                )
            )
            .scalars()
            .all()
        )
        assert "pending" not in pending  # 未完成任务全部取消，已完成的不受影响
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
        assert "terminated" in events


async def test_renewal_links_previous_instance(client, steward_env):
    """续期：同人同包再次获得权益并认领后，新实例挂 renewed_from 链。"""
    ctx = await activate(client, steward_env)
    a, u = ctx["admin"], ctx["user"]
    first_id = ctx["instance"]["id"]

    # 先让第一期终局（退款作废即 terminated，属可续期前置状态）
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        ent = await session.get(MpEntitlement, ctx["entitlement_id"])
        ent.status = EntitlementStatus.VOID.value
        ent.void_reason = "协商退款后重新购买"
        ent.voided_at = datetime.now(timezone.utc)
        await session.commit()

    from tests.steward_helpers import claim_entitlement, issue_batch_entitlement

    ent2 = await issue_batch_entitlement(client, a, ctx["package"])
    await claim_entitlement(client, ent2)

    resp = await client.get(f"{API}/mp/steward/instances", headers=u)
    items = {i["id"]: i for i in resp.json()}
    assert len(items) == 2
    renewed = [i for i in items.values() if i["renewed_from_instance_id"] == first_id]
    assert renewed and renewed[0]["status"] == "preparing"
