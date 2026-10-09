"""健康档案在缺少敏感信息单独同意与删除流程时只允许本地演示写入。"""

import pytest

from app.core.config import settings
from app.core.security import SCOPE_USER, TOKEN_TYPE_ACCESS, create_token


API = "/api/v1/mp/profile"
PAYLOAD = {
    "name": "演示用户",
    "gender": "female",
    "birth": "1990-06",
    "chronic_tags": ["高血压"],
    "allergy": "演示过敏史",
    "surgery_history": "演示手术史",
}


@pytest.mark.asyncio
async def test_profile_write_mode_and_existing_read_only_access(client, monkeypatch):
    existing_user = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, TOKEN_TYPE_ACCESS)}"}
    assert (await client.get(f"{API}/capabilities")).status_code == 401

    monkeypatch.setattr(settings, "APP_ENV", "dev")
    monkeypatch.setattr(settings, "WX_APPID", "")
    monkeypatch.setattr(settings, "WX_SECRET", "")
    assert (await client.get(f"{API}/capabilities", headers=existing_user)).json()["write_mode"] == "demo"
    new_login = await client.post("/api/v1/wx/login", json={"code": "dev-code"})
    assert new_login.status_code == 200
    new_user = {"Authorization": f"Bearer {new_login.json()['token']}"}
    assert (await client.get(API, headers=new_user)).json() is None

    monkeypatch.setattr(settings, "APP_ENV", "test")
    assert (await client.get(f"{API}/capabilities", headers=new_user)).json()["write_mode"] == "demo"
    assert (await client.put(API, json=PAYLOAD, headers=new_user)).status_code == 200

    monkeypatch.setattr(settings, "APP_ENV", "dev")
    empty_login = await client.post("/api/v1/wx/login", json={"code": "dev-code"})
    assert empty_login.status_code == 200
    empty_user = {"Authorization": f"Bearer {empty_login.json()['token']}"}
    assert (await client.get(API, headers=empty_user)).json() is None

    before = (await client.get(API, headers=existing_user)).json()
    assert before is not None
    for environment in ("staging", "prod"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        capability = await client.get(f"{API}/capabilities", headers=existing_user)
        assert capability.status_code == 200
        assert capability.json()["write_mode"] == "closed"
        assert "暂停新建和修改" in capability.json()["message"]

        rejected = await client.put(
            API, json={**PAYLOAD, "allergy": f"{environment} 不应写入"}, headers=existing_user
        )
        assert rejected.status_code == 503
        assert "单独同意" in rejected.json()["detail"]
        assert (await client.get(API, headers=existing_user)).json() == before

        # 没有档案的用户也不能新建，已有本人档案仍可读取。
        assert (await client.get(API, headers=empty_user)).json() is None
        assert (await client.put(API, json=PAYLOAD, headers=empty_user)).status_code == 503
        assert (await client.get(API, headers=empty_user)).json() is None
