"""真实排班未接入时，预发布环境不能暴露演示时段或接受预约。"""

from datetime import date, timedelta

import pytest

from app.core.config import settings
from app.core.security import SCOPE_USER, TOKEN_TYPE_ACCESS, create_token


@pytest.mark.asyncio
async def test_staging_hides_demo_slots_and_rejects_booking(client, monkeypatch):
    token = create_token(1, SCOPE_USER, TOKEN_TYPE_ACCESS)
    headers = {"Authorization": f"Bearer {token}"}
    providers = (await client.get("/api/v1/mp/providers")).json()["items"]
    assert providers

    monkeypatch.setattr(settings, "APP_ENV", "staging")
    detail = await client.get(f"/api/v1/mp/providers/{providers[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["slots"] == []

    before = (await client.get("/api/v1/mp/appointments", headers=headers)).json()["total"]
    booking = await client.post(
        "/api/v1/mp/appointments",
        json={
            "service_id": 1,
            "provider_id": providers[0]["id"],
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "period": "上午",
            "contact_name": "测试用户",
            "contact_phone": "13800000001",
        },
        headers=headers,
    )
    assert booking.status_code == 503
    assert "真实排班" in booking.json()["detail"]
    after = (await client.get("/api/v1/mp/appointments", headers=headers)).json()["total"]
    assert after == before
