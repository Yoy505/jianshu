"""工作二 · 45 天日历、暂停补时、到期 / 退款联动、结案用例。

批次一起（A2）：补时边界口径（23:59:59 / 恰 24h / 24h+1s / 25h）、暂停期间计时停止
（不按 ends_at 过期、current_day 冻结）、中断期滑入过去的任务补交、多次暂停累计；
A3：结案补录字段、普通再次购买不误标续期。
"""

from datetime import date, datetime, time, timedelta, timezone
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
from tests.commerce_helpers import API
from tests.steward_helpers import full_setup

pytestmark = pytest.mark.asyncio
SH = ZoneInfo("Asia/Shanghai")


async def activate(client) -> dict:
    ctx = await full_setup(client)
    resp = await client.post(
        f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"]
    )
    assert resp.status_code == 200, resp.text
    ctx["instance"] = resp.json()
    return ctx


async def test_calendar_nodes_and_dates(client):
    """45 天日历：每日事项、6 次随访、43/44/45 复评结案节点与日期映射正确。"""
    ctx = await activate(client)
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


async def test_user_daily_task_complete_flow(client):
    """本人完成自己的每日任务；管家任务拒绝、重复完成 409。"""
    ctx = await activate(client)
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


async def _backdate_activation(client, instance_id: int, days: int) -> None:
    """把激活时刻与全部任务日期整体回拨 N 天（构造「已服务数日」的时间线）。"""
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        instance = await session.get(StewardServiceInstance, instance_id)
        shift = timedelta(days=days)
        instance.activated_at = instance.activated_at - shift
        instance.ends_at = instance.ends_at - shift
        instance.original_ends_at = instance.original_ends_at - shift
        for task in (
            (
                await session.execute(
                    select(StewardTask).where(StewardTask.instance_id == instance_id)
                )
            )
            .scalars()
            .all()
        ):
            task.service_date = task.service_date - shift
            task.due_at = task.due_at - shift
        await session.commit()


async def test_pause_over_24h_compensates_and_shifts(client):
    """服务方核心中断 >24h：全额补时，ends_at 顺延，未开始任务按天平移留痕。"""
    ctx = await activate(client)
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


async def test_pause_under_24h_records_without_compensation(client):
    """<24h 服务方中断：只记录，不补时、不平移。"""
    ctx = await activate(client)
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


@pytest.mark.parametrize(
    "seconds,expect_compensated",
    [(24 * 3600 - 1, False), (24 * 3600, False), (24 * 3600 + 1, True), (25 * 3600, True)],
)
async def test_pause_compensation_boundaries(client, monkeypatch, seconds, expect_compensated):
    """A2 边界口径（固定时钟精确验证）：23:59:59 与恰 24h 不补时；24h+1s 起、25h 全额补时。"""
    from app.services import steward_service as svc

    # 锚点取真实时间 +1 分钟：须不早于试点放行 effective_from 的库端真实时钟
    # （server_default=func.now()），写死日期会在次日使激活判定失效（时钟定时炸弹）
    fixed_now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
    monkeypatch.setattr(svc, "now_utc", lambda: fixed_now)

    ctx = await full_setup(client)
    a = ctx["admin"]
    instance_id = ctx["instance"]["id"]
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/activate", headers=ctx["user"]
    )
    assert resp.status_code == 200, resp.text
    ends_at_activated = resp.json()["ends_at"]
    ends_before = datetime.fromisoformat(ends_at_activated.replace("Z", "+00:00"))

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "provider_interruption", "reason": f"边界 {seconds}s"},
        headers=a,
    )
    assert resp.status_code == 200
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
        record.started_at = fixed_now - timedelta(seconds=seconds)
        await session.commit()

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/resume", json={}, headers=a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if expect_compensated:
        assert body["compensated_seconds"] == seconds, f"{seconds}s 应全额补时"
        ends_after = datetime.fromisoformat(body["ends_at"].replace("Z", "+00:00"))
        assert ends_after - ends_before == timedelta(seconds=seconds)
    else:
        assert body["compensated_seconds"] == 0, f"{seconds}s 不应补时"
        assert body["ends_at"] == ends_at_activated


async def test_long_pause_not_expired_and_resumable(client):
    """A2：暂停期间计时停止——ends_at 已过也不判过期；恢复补时后 ends_at 越过当前时间。"""
    ctx = await activate(client)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "physician_advised", "reason": "医师建议住院观察，暂停居家计划"},
        headers=a,
    )
    assert resp.status_code == 200
    pause_id = await _backdate_pause(client, hours=72)

    # 把 ends_at 回拨到已过去：paused 状态不受到期懒迁移影响
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        instance = await session.get(StewardServiceInstance, instance_id)
        instance.ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    assert resp.status_code == 200 and resp.json()["status"] == "paused"

    # 恢复：医师建议暂停全额补时 72h，ends_at 重新越过当前时间 → active 可继续
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/resume", json={"note": "出院恢复"}, headers=a
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["compensated_seconds"] >= 72 * 3600 - 60
    ends_after = datetime.fromisoformat(body["ends_at"].replace("Z", "+00:00"))
    assert ends_after > datetime.now(timezone.utc)


async def test_pause_shifts_tasks_fallen_into_past(client):
    """A2：中断期间滑入过去日期的待办一并顺延补交（暂停开始日起全量平移）。"""
    ctx = await activate(client)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]
    await _backdate_activation(client, instance_id, days=3)  # 已服务 3 日的时间线

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "provider_interruption", "reason": "设备云长时间故障"},
        headers=a,
    )
    assert resp.status_code == 200
    await _backdate_pause(client, hours=49)  # 2 天 + 1 小时 → 补时 49h，平移 ceil=3 天

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
        pause_start_date = record.started_at.astimezone(SH).date()
        due_on_pause_day = (
            (
                await session.execute(
                    select(StewardTask).where(
                        StewardTask.instance_id == instance_id,
                        StewardTask.task_type == "daily",
                        StewardTask.service_date == pause_start_date,
                    )
                )
            )
            .scalars()
            .one()
        )
        task_id = due_on_pause_day.id

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/resume", json={"note": "故障修复"}, headers=a
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["compensated_seconds"] >= 49 * 3600 - 60

    # 暂停开始日的任务被顺延 3 天（补交），不再是过去日期
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        task = await session.get(StewardTask, task_id)
        assert task.service_date == pause_start_date + timedelta(days=3)
        assert task.shifted_days == 3
        assert task.status == "pending"  # 补交而非取消


async def test_current_day_frozen_during_pause(client):
    """A2：暂停期间 current_day 冻结在暂停开始日对应服务日。"""
    ctx = await activate(client)
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]
    await _backdate_activation(client, instance_id, days=6)  # 已服务 6 日的时间线

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "user_request", "reason": "本人出差申请暂停"},
        headers=a,
    )
    assert resp.status_code == 200
    await _backdate_pause(client, hours=96)  # 暂停开始日 = 4 天前（服务第 3 天）

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
        pause_start_date = record.started_at.astimezone(SH).date()
        # 冻结服务日 = 暂停开始日（含当日）之前最后一个 daily 的 service_day
        frozen_day = (
            (
                await session.execute(
                    select(StewardTask.service_day)
                    .where(
                        StewardTask.instance_id == instance_id,
                        StewardTask.task_type == "daily",
                        StewardTask.service_date <= pause_start_date,
                    )
                    .order_by(StewardTask.service_day.desc())
                    .limit(1)
                )
            )
        ).scalar_one()

    resp = await client.get(f"{API}/mp/steward/instances/{instance_id}", headers=u)
    assert resp.status_code == 200
    assert resp.json()["current_day"] == frozen_day


async def test_physician_advised_pause_full_compensation(client):
    """医师建议暂停：无论时长全额补时。"""
    ctx = await activate(client)
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


async def test_multiple_pauses_accumulate(client):
    """多次暂停：补偿秒数与 ends_at 顺延逐次累计。"""
    ctx = await activate(client)
    a = ctx["admin"]
    instance_id = ctx["instance"]["id"]
    ends_before = datetime.fromisoformat(ctx["instance"]["ends_at"].replace("Z", "+00:00"))

    # 第一次：医师建议 6h
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "physician_advised", "reason": "短期医嘱暂停"},
        headers=a,
    )
    assert resp.status_code == 200
    await _backdate_pause(client, hours=6)
    resp = await client.post(f"{API}/admin/steward/instances/{instance_id}/resume", json={}, headers=a)
    assert resp.status_code == 200
    after_first = datetime.fromisoformat(resp.json()["ends_at"].replace("Z", "+00:00"))
    assert after_first - ends_before >= timedelta(hours=5.9)

    # 第二次：服务方中断 25h
    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/pause",
        json={"cause": "provider_interruption", "reason": "平台故障"},
        headers=a,
    )
    assert resp.status_code == 200
    await _backdate_pause(client, hours=25)
    resp = await client.post(f"{API}/admin/steward/instances/{instance_id}/resume", json={}, headers=a)
    assert resp.status_code == 200
    body = resp.json()
    assert body["compensated_seconds"] >= 31 * 3600 - 120  # 6h + 25h 累计
    after_second = datetime.fromisoformat(body["ends_at"].replace("Z", "+00:00"))
    assert after_second - ends_before >= timedelta(hours=30.9)


async def test_expiry_close_and_backfill(client):
    """到期懒迁移 expired；未来复评任务登记须补录字段；结案后 closed。"""
    ctx = await activate(client)
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

    # 第 44 天期末复评是未来日期：不带补录字段 → 422（A3）
    resp = await client.get(
        f"{API}/mp/steward/instances/{instance_id}/tasks?service_day=44", headers=u
    )
    review = [t for t in resp.json() if t["task_type"] == "final_review"][0]
    resp = await client.post(
        f"{API}/admin/steward/tasks/{review['id']}/result",
        json={"result": "期末复评：睡眠效率提升。"},
        headers=a,
    )
    assert resp.status_code == 422

    # 带实际发生时间与补录原因 → 登记成功并留痕
    resp = await client.post(
        f"{API}/admin/steward/tasks/{review['id']}/result",
        json={
            "result": "期末复评：睡眠效率提升，周期结束后建议维持当前作息。",
            "actual_occurred_at": datetime.now(timezone.utc).isoformat(),
            "backfill_reason": "服务到期后统一补录第 44 天复评结论",
        },
        headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "done"
    assert resp.json()["backfill_reason"]

    resp = await client.post(
        f"{API}/admin/steward/instances/{instance_id}/close",
        json={"closure_report": "45 天服务完成：6 次随访全部完成，期末复评结论良好，结案归档。"},
        headers=a,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "closed"
    assert resp.json()["closed_at"] is not None


async def test_refund_terminates_instance(client):
    """权益作废（退款联动）→ 实例 terminated、未完成任务取消、激活 409。"""
    ctx = await activate(client)
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


async def test_plain_repurchase_not_marked_renewal(client):
    """A3：普通再次购买（同人同包新权益）不自动挂续期链；renewed_from 恒 null。"""
    ctx = await activate(client)
    a, u = ctx["admin"], ctx["user"]

    from tests.steward_helpers import claim_entitlement, issue_batch_entitlement

    ent2 = await issue_batch_entitlement(client, a, ctx["package"])
    await claim_entitlement(client, ent2)

    resp = await client.get(f"{API}/mp/steward/instances", headers=u)
    items = resp.json()
    assert len(items) == 2
    second = [i for i in items if i["status"] == "preparing"][0]
    assert second["renewed_from_instance_id"] is None  # 显式续期动作才允许挂链
