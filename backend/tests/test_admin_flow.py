"""后台核心流程：登录 → 创建活动 → 审核作品 → 仪表盘统计。"""

import pytest


@pytest.mark.asyncio
async def test_admin_flow(client):
    # ---- 错误凭据 401 ----
    resp = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"

    # ---- 未携带 token 访问受保护接口 401 ----
    resp = await client.get("/api/v1/admin/contests")
    assert resp.status_code == 401

    # ---- 登录成功 ----
    resp = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["admin"]["username"] == "admin"
    assert body["admin"]["role"] == "admin"
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    # ---- me ----
    resp = await client.get("/api/v1/admin/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == body["admin"]["id"]

    # ---- 刷新 token 对 ----
    resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()

    # ---- 创建活动 ----
    resp = await client.post(
        "/api/v1/admin/contests",
        headers=headers,
        json={
            "title": "拾光征文·秋季赛",
            "description": "以秋为题",
            "rules": "原创首发",
            "prizes": "一等奖 1 名",
            "start_at": "2026-09-01T00:00:00Z",
            "end_at": "2026-10-31T23:59:59Z",
        },
    )
    assert resp.status_code == 201
    contest = resp.json()
    assert contest["status"] == "draft"
    assert contest["essay_count"] == 0

    # 创建后可查询到
    resp = await client.get(
        "/api/v1/admin/contests", headers=headers, params={"keyword": "秋季赛"}
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # ---- 审核一篇待审作品 ----
    resp = await client.get(
        "/api/v1/admin/essays", headers=headers, params={"status": "submitted"}
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    essay_id = items[0]["id"]
    assert items[0]["excerpt"]

    resp = await client.post(
        f"/api/v1/admin/essays/{essay_id}/review",
        headers=headers,
        json={"action": "approve", "score": 88, "comment": "不错"},
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["status"] == "approved"
    assert detail["score"] == 88
    assert detail["content"]
    assert detail["reviews"][0]["action"] == "approve"
    assert detail["reviews"][0]["admin_name"] == "系统管理员"

    # ---- 仪表盘统计可用 ----
    resp = await client.get("/api/v1/admin/dashboard/stats", headers=headers)
    assert resp.status_code == 200
    stats = resp.json()
    assert set(stats["totals"]) == {"users", "essays", "contests", "pending_review", "likes"}
    assert stats["totals"]["contests"] == 2
    assert stats["totals"]["pending_review"] == 0
    assert len(stats["essay_trend"]) == 14
    assert any(item["approved"] == 1 for item in stats["essay_trend"])
    assert {"status": "approved", "count": 1} in stats["status_dist"]


@pytest.mark.asyncio
async def test_admin_token_scope_not_interchangeable(client):
    # admin 的 access token 不能当 refresh token 用
    resp = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    token = resp.json()["access_token"]
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
