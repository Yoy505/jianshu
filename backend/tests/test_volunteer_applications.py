"""杏临千行志愿者报名：公开提交校验、限流失败关闭、后台权限与状态流转。"""

import pytest

from app.core.config import settings
from app.core.deps import get_redis
from app.main import app

API = "/api/v1"


def _payload(**overrides) -> dict:
    payload = {
        "name": "陈志愿",
        "phone": "13700001111",
        "city": "天津",
        "birth_year": 1995,
        "occupation": "企业行政",
        "organization": "某科技公司",
        "track": "publicity",
        "offline_per_year": 2,
        "can_station": True,
        "intro": "热衷公益，擅长沟通协调。",
        "consent": True,
    }
    payload.update(overrides)
    return payload


async def _admin_headers(client) -> dict:
    result = await client.post(
        f"{API}/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert result.status_code == 200
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


@pytest.mark.asyncio
async def test_disabled_by_default_and_consent_required(client):
    meta = await client.get(f"{API}/h5/volunteer-applications/meta")
    assert meta.status_code == 200
    assert meta.json()["enabled"] is False
    assert meta.json()["tracks"][0]["key"] == "practice"

    closed = await client.post(
        f"{API}/h5/volunteer-applications", json=_payload()
    )
    assert closed.status_code == 503

    monkeypatch_on = pytest.MonkeyPatch()
    monkeypatch_on.setattr(settings, "VOLUNTEER_APPLICATIONS_ENABLED", True)
    try:
        no_consent = await client.post(
            f"{API}/h5/volunteer-applications", json=_payload(consent=False)
        )
        assert no_consent.status_code == 422
        assert "信息使用说明" in no_consent.json()["detail"]
    finally:
        monkeypatch_on.undo()


@pytest.mark.asyncio
async def test_submit_then_admin_lists_and_updates_status(client, monkeypatch):
    monkeypatch.setattr(settings, "VOLUNTEER_APPLICATIONS_ENABLED", True)
    created = await client.post(f"{API}/h5/volunteer-applications", json=_payload())
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["receipt_no"].startswith("VOL-")
    assert set(receipt) == {"id", "receipt_no", "submitted_at"}

    # 校验失败路径：手机号 / 轨道 / 年参与次数
    bad_phone = await client.post(
        f"{API}/h5/volunteer-applications", json=_payload(phone="12345")
    )
    assert bad_phone.status_code == 422
    # track=other 必须填写 track_note
    no_note = await client.post(
        f"{API}/h5/volunteer-applications", json=_payload(track="other")
    )
    assert no_note.status_code == 422
    assert "具体服务方向" in no_note.json()["detail"]
    bad_times = await client.post(
        f"{API}/h5/volunteer-applications", json=_payload(offline_per_year=0)
    )
    assert bad_times.status_code == 422

    # track=other + 自由填写方向 → 成功并可读回
    other_ok = await client.post(
        f"{API}/h5/volunteer-applications",
        json=_payload(name="李其他", phone="13600003333", track="other", track_note="应急救援保障"),
    )
    assert other_ok.status_code == 201
    other_id = other_ok.json()["id"]

    # editor 无权查看
    editor_login = await client.post(
        f"{API}/admin/auth/login", json={"username": "editor", "password": "editor123"}
    )
    editor = {"Authorization": f"Bearer {editor_login.json()['access_token']}"}
    assert (await client.get(f"{API}/admin/volunteer-applications", headers=editor)).status_code == 403

    admin = await _admin_headers(client)
    listing = await client.get(f"{API}/admin/volunteer-applications", headers=admin)
    assert listing.status_code == 200
    assert listing.json()["total"] == 2
    item = next(i for i in listing.json()["items"] if i["id"] == receipt["id"])
    assert item["phone_masked"] == "137****1111"
    # 使用允许字段白名单，防止完整资料重新混入分页列表。
    list_fields = {
        "id", "receipt_no", "name", "phone_masked", "city", "track",
        "can_station", "offline_per_year", "status", "submitted_at",
    }
    assert all(set(row) == list_fields for row in listing.json()["items"])
    assert item["status"] == "pending"

    detail = await client.get(
        f"{API}/admin/volunteer-applications/{receipt['id']}", headers=admin
    )
    assert detail.status_code == 200
    assert detail.json()["phone"] == "13700001111"
    assert detail.json()["consent_text"]
    assert detail.json()["organization"] == "某科技公司"
    assert detail.json()["intro"] == _payload()["intro"]
    assert "gender" not in detail.json()

    other_detail = await client.get(f"{API}/admin/volunteer-applications/{other_id}", headers=admin)
    assert other_detail.json()["track"] == "other"
    assert other_detail.json()["track_note"] == "应急救援保障"

    # 详情的明文访问必须有操作人及记录ID留痕。
    audit_logs = await client.get(
        f"{API}/admin/audit-logs", headers=admin,
        params={"action": "volunteer.detail_read", "resource_type": "volunteer_application"},
    )
    assert audit_logs.status_code == 200
    reads = audit_logs.json()["items"]
    assert {row["resource_id"] for row in reads} == {receipt["id"], other_id}
    assert all(row["actor_name"] == "系统管理员" and row["created_at"] for row in reads)

    # 列表收窄不影响按回执、单位搜索及状态筛选。
    for query in ({"q": receipt["receipt_no"]}, {"q": "科技", "status": "pending"}):
        filtered = await client.get(f"{API}/admin/volunteer-applications", headers=admin, params=query)
        assert filtered.status_code == 200
        assert filtered.json()["items"]
        assert all(set(row) == list_fields for row in filtered.json()["items"])

    for suffix in ("", f"/{receipt['id']}"):
        assert (await client.get(f"{API}/admin/volunteer-applications{suffix}")).status_code == 401
        assert (await client.get(f"{API}/admin/volunteer-applications{suffix}", headers=editor)).status_code == 403
    for headers, expected in (({}, 401), (editor, 403)):
        denied = await client.post(
            f"{API}/admin/volunteer-applications/{receipt['id']}/status",
            headers=headers, json={"status": "enrolled"},
        )
        assert denied.status_code == expected

    bad_status = await client.post(
        f"{API}/admin/volunteer-applications/{receipt['id']}/status",
        headers=admin, json={"status": "unknown"},
    )
    assert bad_status.status_code == 422
    contacted = await client.post(
        f"{API}/admin/volunteer-applications/{receipt['id']}/status",
        headers=admin, json={"status": "contacted"},
    )
    assert contacted.status_code == 200
    assert contacted.json()["status"] == "contacted"
    enrolled = await client.post(
        f"{API}/admin/volunteer-applications/{receipt['id']}/status",
        headers=admin, json={"status": "enrolled"},
    )
    assert enrolled.json()["status"] == "enrolled"


@pytest.mark.asyncio
async def test_phone_rate_limit_and_redis_down_fail_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "VOLUNTEER_APPLICATIONS_ENABLED", True)
    first = await client.post(
        f"{API}/h5/volunteer-applications", json=_payload(name="第一次")
    )
    assert first.status_code == 201
    # 同手机号第 4 次提交被拒（3 次/日）
    for i in range(2, 4):
        ok = await client.post(f"{API}/h5/volunteer-applications", json=_payload(name=f"第{i}次"))
        assert ok.status_code == 201
    fourth = await client.post(
        f"{API}/h5/volunteer-applications", json=_payload(name="第四次")
    )
    assert fourth.status_code == 429

    # Redis 不可用 → 503 拒绝（失败关闭）
    class DownRedis:
        async def incr(self, key):
            raise ConnectionError("redis down")

    app.dependency_overrides[get_redis] = lambda: DownRedis()
    try:
        down = await client.post(
            f"{API}/h5/volunteer-applications", json=_payload(phone="13800002222")
        )
        assert down.status_code == 503
    finally:
        app.dependency_overrides.pop(get_redis, None)
