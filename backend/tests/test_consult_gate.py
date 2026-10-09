"""正式环境未开放健康对话前，REST 创建和发言都不能写入。"""

import pytest

from app.core.config import settings
from app.core.security import SCOPE_USER, create_token


API = "/api/v1/mp/consults"


@pytest.mark.asyncio
async def test_consult_rest_writes_closed_in_staging_and_production(client, monkeypatch):
    headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}
    existing = await client.get(API, headers=headers)
    assert existing.status_code == 200
    consult = existing.json()["items"][0]
    consult_id = consult["id"]
    before_messages = await client.get(f"{API}/{consult_id}/messages", headers=headers)
    assert before_messages.status_code == 200
    before_ids = [message["id"] for message in before_messages.json()]
    before_total = existing.json()["total"]
    create_body = {
        "service_id": consult["service"]["id"],
        "provider_id": consult["provider"]["id"],
        "content": "测试消息：不应写入正式环境",
    }

    for environment in ("staging", "prod"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        created = await client.post(API, json=create_body, headers=headers)
        assert created.status_code == 503
        assert "健康信息授权与真人服务流程尚未接入" in created.json()["detail"]

        sent = await client.post(
            f"{API}/{consult_id}/messages", json={"content": "不应保存的健康对话"}, headers=headers
        )
        assert sent.status_code == 503
        assert "健康信息授权与真人服务流程尚未接入" in sent.json()["detail"]

        after_consults = await client.get(API, headers=headers)
        after_messages = await client.get(f"{API}/{consult_id}/messages", headers=headers)
        assert after_consults.json()["total"] == before_total
        assert [message["id"] for message in after_messages.json()] == before_ids

    monkeypatch.setattr(settings, "APP_ENV", "test")
    allowed = await client.post(
        f"{API}/{consult_id}/messages", json={"content": "测试环境演示消息"}, headers=headers
    )
    assert allowed.status_code == 201
    assert allowed.json()["system_message"]["sender"] == "system"
