"""健康域管理后台（/admin/mp）：状态机、审计、过滤（契约 api.md 第三部分）。"""

import pytest


@pytest.mark.asyncio
async def test_mp_admin_appointment_lifecycle_and_audit(client):
    resp = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # ---- 概览 ----
    resp = await client.get("/api/v1/admin/mp/stats", headers=headers)
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["appointments"]["total"] >= 2  # seed：confirmed + completed
    assert stats["providers_total"] == 8
    assert stats["drugs_total"] == 14

    # ---- 用户侧新预约（走完整状态机）----
    import os

    os.environ["TEST_PHONE"] = "13800000001"
    # 直接用用户 token 创建预约（conftest 已有种子女用户）
    from app.core.security import create_token, SCOPE_USER

    user_token = create_token(1, SCOPE_USER, "access")
    user_headers = {"Authorization": f"Bearer {user_token}"}

    # 找一个可预约组合
    resp = await client.get("/api/v1/mp/services?scenario=rehab", headers=headers)
    service_id = resp.json()["items"][0]["id"]
    resp = await client.get(f"/api/v1/mp/providers?service_id={service_id}", headers=user_headers)
    provider_id = resp.json()["items"][0]["id"]

    from datetime import date, timedelta

    appt_date = (date.today() + timedelta(days=1)).isoformat()
    user_token = create_token(1, SCOPE_USER, "access")
    user_headers = {"Authorization": f"Bearer {user_token}"}
    resp = await client.post(
        "/api/v1/mp/appointments",
        json={
            "service_id": service_id,
            "provider_id": provider_id,
            "date": appt_date,
            "period": "上午",
            "contact_name": "测试联系人",
            "contact_phone": "13800000001",
            "address": "天津市滨海新区",
        },
        headers=user_headers,
    )
    assert resp.status_code == 201
    appt_id = resp.json()["id"]

    # ---- 管理列表（含联系人搜索）----
    resp = await client.get("/api/v1/admin/mp/appointments?q=测试联系人", headers=headers)
    assert resp.status_code == 200
    assert any(item["id"] == appt_id for item in resp.json()["items"])
    item = next(i for i in resp.json()["items"] if i["id"] == appt_id)
    assert item["contact_phone"] == "13800000001"  # 完整手机（线下联络）
    assert item["user"]["phone"].startswith("138****")  # 用户手机掩码

    # ---- 状态机：pending→confirmed→completed；非法迁移 409 ----
    resp = await client.post(f"/api/v1/admin/mp/appointments/{appt_id}/confirm", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "confirmed"
    resp = await client.post(f"/api/v1/admin/mp/appointments/{appt_id}/confirm", headers=headers)
    assert resp.status_code == 409
    resp = await client.post(f"/api/v1/admin/mp/appointments/{appt_id}/complete", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "completed"
    resp = await client.post(f"/api/v1/admin/mp/appointments/{appt_id}/cancel", headers=headers)
    assert resp.status_code == 409

    # ---- 审计留痕 ----
    resp = await client.get(
        "/api/v1/admin/audit-logs?resource_type=mp_appointment", headers=headers
    )
    actions = [i["action"] for i in resp.json()["items"]]
    assert "mp.appointment.confirm" in actions
    assert "mp.appointment.complete" in actions


@pytest.mark.asyncio
async def test_mp_admin_consult_provider_drug_notice(client):
    resp = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # ---- 咨询列表 + 详情 + 关闭 ----
    resp = await client.get("/api/v1/admin/mp/consults?status=open", headers=headers)
    assert resp.status_code == 200
    open_items = resp.json()["items"]
    assert len(open_items) >= 1  # seed 的演示会话
    consult_id = open_items[0]["id"]
    assert open_items[0]["message_count"] >= 1

    resp = await client.get(f"/api/v1/admin/mp/consults/{consult_id}", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["messages"]) >= 1

    resp = await client.post(f"/api/v1/admin/mp/consults/{consult_id}/close", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "closed"
    resp = await client.post(f"/api/v1/admin/mp/consults/{consult_id}/close", headers=headers)
    assert resp.status_code == 409

    # ---- 人员：在线过滤 + 更新 ----
    resp = await client.get("/api/v1/admin/mp/providers?online=true", headers=headers)
    assert resp.status_code == 200
    assert all(p["online"] for p in resp.json()["items"])
    provider_id = resp.json()["items"][0]["id"]

    resp = await client.patch(
        f"/api/v1/admin/mp/providers/{provider_id}",
        json={"online": False, "intro": "（管理员更新）出诊时间以平台公告为准。"},
        headers=headers,
    )
    assert resp.status_code == 200 and resp.json()["online"] is False

    # ---- 服务：过滤 + 改价 + 下架 ----
    resp = await client.get("/api/v1/admin/mp/services?service_type=online", headers=headers)
    assert resp.status_code == 200
    service_id = resp.json()["items"][0]["id"]
    resp = await client.patch(
        f"/api/v1/admin/mp/services/{service_id}", json={"price": 4900}, headers=headers
    )
    assert resp.status_code == 200 and resp.json()["price"] == 4900

    # ---- 药品：详情 + 留言删除 ----
    resp = await client.get("/api/v1/admin/mp/drugs?tag=rx", headers=headers)
    assert resp.status_code == 200
    drug_id = resp.json()["items"][0]["id"]
    resp = await client.get(f"/api/v1/admin/mp/drugs/{drug_id}", headers=headers)
    assert resp.status_code == 200
    board = resp.json()["board"]
    if board:
        feedback_id = board[0]["id"]
        resp = await client.delete(
            f"/api/v1/admin/mp/drugs/feedback/{feedback_id}", headers=headers
        )
        assert resp.status_code == 204

    # ---- 公告：创建 → C 端可见 → 删除 ----
    resp = await client.post(
        "/api/v1/admin/mp/notices", json={"content": "测试公告：今晚 20:00 义诊直播"}, headers=headers
    )
    assert resp.status_code == 201
    notice_id = resp.json()["id"]
    resp = await client.get("/api/v1/mp/home")
    assert any(n["content"].startswith("测试公告") for n in resp.json()["notices"])
    resp = await client.delete(f"/api/v1/admin/mp/notices/{notice_id}", headers=headers)
    assert resp.status_code == 204

    # ---- 未登录 401 ----
    resp = await client.get("/api/v1/admin/mp/stats")
    assert resp.status_code == 401
