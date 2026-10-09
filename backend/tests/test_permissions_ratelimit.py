"""权限矩阵 + 用户级限流 + 预约防重（契约：api.md 通用约定两节）。"""

import pytest


async def _login(client, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/v1/admin/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"{username} 登录失败"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_editor_permission_boundary(client):
    """编辑：可审核/可健康域运营；不可删活动、封用户、读审计。"""
    editor = await _login(client, "editor", "editor123")

    # ✓ 审核（essay.review）
    resp = await client.get("/api/v1/admin/essays?status=submitted", headers=editor)
    assert resp.status_code == 200
    essay_id = resp.json()["items"][0]["id"]
    resp = await client.post(
        f"/api/v1/admin/essays/{essay_id}/review",
        json={"action": "approve"},
        headers=editor,
    )
    assert resp.status_code == 200

    # ✓ 健康域运营（mp.manage）：确认预约
    resp = await client.get("/api/v1/admin/mp/consults", headers=editor)
    assert resp.status_code == 200

    # ✓ 活动编辑（contest.manage）
    resp = await client.post(
        "/api/v1/admin/contests/1/status", json={"status": "published"}, headers=editor
    )
    assert resp.status_code == 200

    # ✗ 删除活动（contest.delete）
    resp = await client.delete("/api/v1/admin/contests/999", headers=editor)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "无权限执行该操作"

    # ✗ 封禁用户（user.manage）
    resp = await client.post("/api/v1/admin/users/1/ban", headers=editor)
    assert resp.status_code == 403

    # ✗ 审计日志（audit.read）
    resp = await client.get("/api/v1/admin/audit-logs", headers=editor)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_has_all_permissions(client):
    admin = await _login(client, "admin", "admin123")
    resp = await client.get("/api/v1/admin/audit-logs", headers=admin)
    assert resp.status_code == 200
    resp = await client.post("/api/v1/admin/users/1/unban", headers=admin)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_appointment_duplicate_guard(client):
    """同人同日同时段已有进行中预约 → 409；取消后可再约。"""
    from datetime import date, timedelta

    from app.core.security import SCOPE_USER, create_token

    user_headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}

    # 找可预约组合（seed：服务 8 护师上门 ↔ 人员 3）
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    payload = {
        "service_id": 8,
        "provider_id": 3,
        "date": tomorrow,
        "period": "下午",
        "contact_name": "防重测试",
        "contact_phone": "13800000001",
        "address": "天津市滨海新区",
    }
    resp = await client.post("/api/v1/mp/appointments", json=payload, headers=user_headers)
    assert resp.status_code == 201
    appt_id = resp.json()["id"]

    # 同日同时段再约 → 409（换服务/人员也拦）
    payload2 = dict(payload, service_id=1, provider_id=1)
    resp = await client.post("/api/v1/mp/appointments", json=payload2, headers=user_headers)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "该时段已有进行中的预约"

    # 不同时段不受影响
    resp = await client.post(
        "/api/v1/mp/appointments", json=dict(payload, period="晚间"), headers=user_headers
    )
    assert resp.status_code == 201

    # 取消后同时段可再约
    resp = await client.post(f"/api/v1/mp/appointments/{appt_id}/cancel", headers=user_headers)
    assert resp.status_code == 200
    resp = await client.post("/api/v1/mp/appointments", json=payload, headers=user_headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_retired_literary_public_api_is_unavailable(client):
    """旧文学作品接口不再挂载；小程序用户令牌也不能向旧活动投稿。"""
    resp = await client.post("/api/v1/wx/login", json={"code": "dev-code"})
    assert resp.status_code == 200
    user_headers = {"Authorization": f"Bearer {resp.json()['token']}"}
    for path in ("/api/v1/h5/contests/current", "/api/v1/h5/essays"):
        assert (await client.get(path)).status_code == 404
    resp = await client.post(
        "/api/v1/h5/essays",
        json={"contest_id": 1, "title": "旧文学投稿", "content": "文" * 150},
        headers=user_headers,
    )
    assert resp.status_code == 404
