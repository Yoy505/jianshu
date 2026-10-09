"""审计日志：登录/审核留痕 + 查询接口（契约见 docs/api.md 五之二）。"""

import pytest


@pytest.mark.asyncio
async def test_audit_trail(client):
    # ---- 登录（应写入 admin.login）----
    resp = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # ---- 审核一篇待审作品（应写入 essay.review）----
    resp = await client.get("/api/v1/admin/essays?status=submitted", headers=headers)
    assert resp.status_code == 200
    essay_id = resp.json()["items"][0]["id"]
    resp = await client.post(
        f"/api/v1/admin/essays/{essay_id}/review",
        json={"action": "approve", "score": 90},
        headers=headers,
    )
    assert resp.status_code == 200

    # ---- 封禁再解封（应写入 user.ban / user.unban）----
    resp = await client.get("/api/v1/admin/users", headers=headers)
    user_id = resp.json()["items"][0]["id"]
    assert (
        await client.post(f"/api/v1/admin/users/{user_id}/ban", headers=headers)
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/admin/users/{user_id}/unban", headers=headers)
    ).status_code == 200

    # ---- 审计列表：按时间倒序，最近一次是 user.unban ----
    resp = await client.get("/api/v1/admin/audit-logs", headers=headers)
    assert resp.status_code == 200
    page = resp.json()
    actions = [item["action"] for item in page["items"]]
    assert page["total"] >= 4
    assert actions[0] == "user.unban"
    for expected in ("admin.login", "essay.review", "user.ban"):
        assert expected in actions

    # 记录结构完整
    sample = next(item for item in page["items"] if item["action"] == "essay.review")
    assert sample["actor_type"] == "admin"
    assert sample["actor_name"]  # 冗余快照非空
    assert sample["resource_id"] == essay_id
    assert sample["detail"]["to"] == "approved"
    assert sample["detail"]["score"] == 90
    assert sample["ip_address"]  # 测试客户端有 host

    # ---- action 过滤 ----
    resp = await client.get(
        "/api/v1/admin/audit-logs?action=admin.login", headers=headers
    )
    assert resp.status_code == 200
    assert {item["action"] for item in resp.json()["items"]} == {"admin.login"}

    # ---- resource_type 过滤 ----
    resp = await client.get(
        "/api/v1/admin/audit-logs?resource_type=user", headers=headers
    )
    assert resp.status_code == 200
    assert all(item["resource_type"] == "user" for item in resp.json()["items"])

    # ---- 未登录不可读 ----
    resp = await client.get("/api/v1/admin/audit-logs")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_audit_rolls_back_with_business_failure(client):
    """业务失败（404/409）时不得留下审计记录：审计与业务同事务。"""
    resp = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # 不存在的作品 → 404，不应写 essay.review
    resp = await client.post(
        "/api/v1/admin/essays/999999/review",
        json={"action": "approve"},
        headers=headers,
    )
    assert resp.status_code == 404

    resp = await client.get("/api/v1/admin/audit-logs", headers=headers)
    actions = [i["action"] for i in resp.json()["items"]]
    assert "essay.review" not in actions
    assert "admin.login" in actions
