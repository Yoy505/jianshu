"""批次十一：岗位数据范围（B①）与随访预约意向（B③）。"""
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AdminUser, StewardScheduleIntent, StewardTask, StewardTimelineEvent
from conftest import FakeRedis  # noqa: F401  # 确保 conftest 加载
from tests.commerce_helpers import API, admin_headers
from tests.steward_helpers import USER1, full_setup, user_headers


async def _activate(ctx: dict) -> dict:
    resp = await ctx["client"].post(
        f"{API}/mp/steward/instances/{ctx['instance']['id']}/activate", headers=ctx["user"]
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def two_instances(client):
    """两个实例、两套互不相同的岗位指派（同属用户 1，范围测试按岗位不按用户）。"""
    ctx1 = await full_setup(client)
    ctx1["client"] = client
    await _activate(ctx1)
    ctx2 = await full_setup(client)
    ctx2["client"] = client
    await _activate(ctx2)
    return ctx1, ctx2


async def _bind_editor_to(client, a: dict, staff_id: int) -> None:
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        editor = (
            (await session.execute(select(AdminUser).where(AdminUser.username == "editor")))
            .scalars()
            .one()
        )
        editor_id = editor.id
    resp = await client.post(
        f"{API}/admin/steward/staff-links", json={"staff_id": staff_id, "admin_id": editor_id},
        headers=a,
    )
    assert resp.status_code == 201, resp.text


# ───────────────────────── B① 岗位数据范围 ─────────────────────────


@pytest.mark.asyncio
async def test_instance_scope_by_staff_binding(client, two_instances):
    ctx1, ctx2 = two_instances
    a = ctx1["admin"]
    editor = await admin_headers(client, "editor")

    # 未绑定的只读账号：fail-closed，看不到任何业务数据
    resp = await client.get(f"{API}/admin/steward/instances", headers=editor)
    assert resp.status_code == 200 and resp.json()["total"] == 0

    # 管理账号（steward.manage）：全局可见
    resp = await client.get(f"{API}/admin/steward/instances", headers=a)
    assert resp.json()["total"] == 2

    # 绑定为实例 1 的管家 → 只见实例 1；实例 2 详情/日历/时间线 404
    await _bind_editor_to(client, a, ctx1["staff"]["butler"]["id"])
    resp = await client.get(f"{API}/admin/steward/instances", headers=editor)
    body = resp.json()
    assert body["total"] == 1 and body["items"][0]["id"] == ctx1["instance"]["id"]

    for path in (
        f"/admin/steward/instances/{ctx2['instance']['id']}",
        f"/admin/steward/instances/{ctx2['instance']['id']}/calendar?month=2026-11",
        f"/admin/steward/instances/{ctx2['instance']['id']}/timeline",
    ):
        resp = await client.get(f"{API}{path}", headers=editor)
        assert resp.status_code == 404, f"{path} -> {resp.status_code}"
    # 本人范围内实例详情可见
    resp = await client.get(
        f"{API}/admin/steward/instances/{ctx1['instance']['id']}", headers=editor
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_professional_order_scope_physician_only(client, two_instances):
    ctx1, ctx2 = two_instances
    a = ctx1["admin"]
    editor = await admin_headers(client, "editor")

    for ctx, title in ((ctx1, "复核一"), (ctx2, "复核二")):
        resp = await client.post(
            f"{API}/admin/steward/professional/orders",
            json={
                "instance_id": ctx["instance"]["id"], "order_type": "physician_review",
                "title": title, "priority": "normal", "physician_id": ctx["staff"]["physician"]["id"],
            },
            headers=a,
        )
        assert resp.status_code == 201, resp.text

    # 绑定管家（非医师）→ 工单零可见（医学隔离）
    await _bind_editor_to(client, a, ctx1["staff"]["butler"]["id"])
    resp = await client.get(f"{API}/admin/steward/professional/orders", headers=editor)
    assert resp.json()["total"] == 0

    # 追加绑定为医师 → 看到该医师全部工单（full_setup 门禁要求同用户同医师，
    # 两实例共用医师，故两单均可见）；详情可见
    await _bind_editor_to(client, a, ctx1["staff"]["physician"]["id"])
    resp = await client.get(f"{API}/admin/steward/professional/orders", headers=editor)
    body = resp.json()
    assert body["total"] == 2
    order1_id = (
        await client.get(f"{API}/admin/steward/professional/orders", headers=a)
    ).json()["items"][0]["id"]
    resp = await client.get(f"{API}/admin/steward/professional/orders/{order1_id}", headers=editor)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_workbench_scope(client, two_instances):
    ctx1, _ = two_instances
    editor = await admin_headers(client, "editor")
    await _bind_editor_to(client, ctx1["admin"], ctx1["staff"]["butler"]["id"])
    resp = await client.get(f"{API}/admin/steward/workbench", headers=editor)
    assert resp.status_code == 200
    body = resp.json()
    assert sum(body["instances"].values()) == 1


# ───────────────────────── B③ 随访预约意向 ─────────────────────────


@pytest_asyncio.fixture
async def active_followup(client):
    ctx = await full_setup(client)
    ctx["client"] = client
    await _activate(ctx)
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        followup = (
            (
                await session.execute(
                    select(StewardTask).where(
                        StewardTask.instance_id == ctx["instance"]["id"],
                        StewardTask.task_type == "follow_up",
                        StewardTask.status == "pending",
                    )
                )
            )
            .scalars()
            .first()
        )
        daily = (
            (
                await session.execute(
                    select(StewardTask).where(
                        StewardTask.instance_id == ctx["instance"]["id"],
                        StewardTask.task_type == "daily",
                        StewardTask.status == "pending",
                    )
                )
            )
            .scalars()
            .first()
        )
        assert followup is not None and daily is not None
        ctx["followup_id"] = followup.id
        ctx["daily_id"] = daily.id
        ctx["followup_date"] = followup.service_date
    return ctx


def _intent_payload(task_id: int, date_str: str, period: str = "上午") -> dict:
    return {
        "task_id": task_id,
        "preferred_date": date_str,
        "preferred_period": period,
        "note": "方便接听",
    }


@pytest.mark.asyncio
async def test_schedule_intent_submit_and_supersede(client, active_followup):
    ctx = active_followup
    u = ctx["user"]
    instance_id = ctx["instance"]["id"]
    date1 = ctx["followup_date"].isoformat()
    date2 = (ctx["followup_date"] + timedelta(days=1)).isoformat()

    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{ctx['followup_id']}/schedule-intent",
        json=_intent_payload(ctx["followup_id"], date1), headers=u,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"

    # 时间线留痕
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        events = (
            (
                await session.execute(
                    select(StewardTimelineEvent).where(
                        StewardTimelineEvent.instance_id == instance_id,
                        StewardTimelineEvent.event_type == "schedule_intent",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert events and events[0].actor_type == "user"

    # 重复提交 → 旧 pending 置 superseded，新意向生效
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{ctx['followup_id']}/schedule-intent",
        json=_intent_payload(ctx["followup_id"], date2, "下午"), headers=u,
    )
    assert resp.status_code == 200
    async with client.session_factory() as session:  # type: ignore[attr-defined]
        rows = (
            (
                await session.execute(
                    select(StewardScheduleIntent).where(
                        StewardScheduleIntent.task_id == ctx["followup_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert sorted(r.status for r in rows) == ["pending", "superseded"]


@pytest.mark.asyncio
async def test_schedule_intent_validation(client, active_followup):
    ctx = active_followup
    u = ctx["user"]
    instance_id = ctx["instance"]["id"]
    followup_id = ctx["followup_id"]

    # 非随访任务 → 422
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{ctx['daily_id']}/schedule-intent",
        json=_intent_payload(ctx["daily_id"], ctx["followup_date"].isoformat()), headers=u,
    )
    assert resp.status_code == 422
    # 昨日 → 422（以服务日口径的今天为锚，任务服务日多在未来）
    from app.services.steward_service import sh_today

    yesterday = (sh_today() - timedelta(days=1)).isoformat()
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{followup_id}/schedule-intent",
        json=_intent_payload(followup_id, yesterday), headers=u,
    )
    assert resp.status_code == 422
    # 跨用户 → 404
    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{followup_id}/schedule-intent",
        json=_intent_payload(followup_id, ctx["followup_date"].isoformat()),
        headers=user_headers(USER1["id"] + 999),
    )
    assert resp.status_code == 401 or resp.status_code == 404


@pytest.mark.asyncio
async def test_schedule_intent_admin_handle_and_counter(client, active_followup):
    ctx = active_followup
    a, u = ctx["admin"], ctx["user"]
    instance_id = ctx["instance"]["id"]
    followup_id = ctx["followup_id"]

    # 工作台计数：提交前 0
    resp = await client.get(f"{API}/admin/steward/workbench", headers=a)
    assert resp.json()["schedule_intents_pending"] == 0

    resp = await client.post(
        f"{API}/mp/steward/instances/{instance_id}/tasks/{followup_id}/schedule-intent",
        json=_intent_payload(followup_id, ctx["followup_date"].isoformat()), headers=u,
    )
    intent_id = resp.json()["id"]

    resp = await client.get(f"{API}/admin/steward/workbench", headers=a)
    assert resp.json()["schedule_intents_pending"] == 1

    # 后台列表：admin 可见 1 条；未绑定 editor fail-closed 看不到
    editor = await admin_headers(client, "editor")
    resp = await client.get(f"{API}/admin/steward/schedule-intents?status=pending", headers=editor)
    assert resp.status_code == 200 and resp.json() == []
    resp = await client.get(f"{API}/admin/steward/schedule-intents?status=pending", headers=a)
    assert resp.status_code == 200 and len(resp.json()) == 1

    resp = await client.post(
        f"{API}/admin/steward/schedule-intents/{intent_id}/handle",
        json={"note": "已电话确认改约"}, headers=a,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "handled"
    # 重复处理 → 409
    resp = await client.post(
        f"{API}/admin/steward/schedule-intents/{intent_id}/handle", json={}, headers=a
    )
    assert resp.status_code == 409
    # 处理后计数归零
    resp = await client.get(f"{API}/admin/steward/workbench", headers=a)
    assert resp.json()["schedule_intents_pending"] == 0
