"""药品留言在没有医师核验与内容审核流程时只能用于本地演示。"""

import pytest

from app.core.config import settings
from app.core.security import SCOPE_USER, create_token


API = "/api/v1/mp/drugs/1"
DEMO_CONTENT = "这是一条仅供本地功能测试的演示留言，不包含真实病情或个人用药信息。"


@pytest.mark.asyncio
async def test_drug_feedback_is_demo_only_and_hidden_from_public_environments(
    client, monkeypatch
):
    auth_headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    posted = await client.post(
        f"{API}/feedback", json={"content": DEMO_CONTENT}, headers=auth_headers
    )
    assert posted.status_code == 201

    demo_detail = await client.get(API, headers=auth_headers)
    assert demo_detail.status_code == 200
    assert demo_detail.json()["feedback_mode"] == "demo"
    assert demo_detail.json()["brand_name"] is not None
    assert [entry["content"] for entry in demo_detail.json()["board"]] == [DEMO_CONTENT]

    for environment in ("staging", "prod"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        detail = await client.get(API, headers=auth_headers)
        assert detail.status_code == 404  # Legacy demo drug is not publicly listed.

        rejected = await client.post(
            f"{API}/feedback", json={"content": DEMO_CONTENT}, headers=auth_headers
        )
        assert rejected.status_code == 404

    monkeypatch.setattr(settings, "APP_ENV", "test")
    restored = await client.get(API, headers=auth_headers)
    assert restored.json()["feedback_mode"] == "demo"
    assert [entry["content"] for entry in restored.json()["board"]] == [DEMO_CONTENT]
