"""Drug status gates apply to every C-side read and write path."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.core.deps import get_db
from app.core.security import SCOPE_USER, create_token
from app.main import app
from app.models import MpDrug


API = "/api/v1/mp"
FEEDBACK = "这是一段专供本地测试的演示文字，不涉及任何真实诊疗或个人用药信息。"


async def _set_test_statuses() -> None:
    db_override = app.dependency_overrides[get_db]
    async for db in db_override():
        await db.execute(
            update(MpDrug)
            .where(MpDrug.id == 1)
            .values(
                publication_status="published",
                approval_no="TEST-APPROVAL-001",
                specification="测试规格",
                license_holder="测试持有人",
                source_name="测试来源，非官方资料",
                source_url="https://example.org/test-drug",
                source_checked_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
            )
        )
        await db.execute(
            update(MpDrug).where(MpDrug.id == 2).values(publication_status="pending")
        )
        await db.execute(
            update(MpDrug).where(MpDrug.id == 3).values(publication_status="offline")
        )
        await db.commit()


@pytest.mark.asyncio
async def test_drug_publication_filters_all_c_side_paths_and_admin_can_audit(
    client, monkeypatch
):
    user_headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, 'access')}"}
    # Existing favorites stay private after a drug becomes pending or offline.
    for drug_id in (2, 3):
        saved = await client.post(f"{API}/drugs/{drug_id}/favorite", headers=user_headers)
        assert saved.status_code == 200
    await _set_test_statuses()

    test_list = await client.get(f"{API}/drugs", params={"page_size": 30})
    assert test_list.status_code == 200
    assert {d["id"] for d in test_list.json()["items"]}.isdisjoint({2, 3})
    assert 1 in {d["id"] for d in test_list.json()["items"]}
    public_detail = await client.get(f"{API}/drugs/1", headers=user_headers)
    assert public_detail.status_code == 200
    assert public_detail.json()["publication_status"] == "published"
    assert public_detail.json()["approval_no"] == "TEST-APPROVAL-001"
    assert public_detail.json()["source_name"] == "测试来源，非官方资料"
    assert public_detail.json()["source_checked_at"] == "2026-09-24T00:00:00Z"
    assert public_detail.json()["feedback_mode"] == "closed"
    assert public_detail.json()["brand_name"] is None

    for drug_id in (2, 3):
        assert (await client.get(f"{API}/drugs/{drug_id}", headers=user_headers)).status_code == 404
        assert (
            await client.post(f"{API}/drugs/{drug_id}/favorite", headers=user_headers)
        ).status_code == 404
        assert (
            await client.post(
                f"{API}/drugs/{drug_id}/feedback",
                json={"content": FEEDBACK},
                headers=user_headers,
            )
        ).status_code == 404
    favorites = await client.get(f"{API}/favorites", headers=user_headers)
    assert favorites.status_code == 200
    assert favorites.json()["items"] == []
    assert favorites.json()["total"] == 0

    admin_login = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert admin_login.status_code == 200
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    admin_list = await client.get(
        "/api/v1/admin/mp/drugs", params={"page_size": 30}, headers=admin_headers
    )
    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == 14
    admin_items = {d["id"]: d for d in admin_list.json()["items"]}
    assert admin_items[2]["publication_status"] == "pending"
    assert admin_items[3]["publication_status"] == "offline"
    assert admin_items[1]["source_url"] == "https://example.org/test-drug"
    pending = await client.get(
        "/api/v1/admin/mp/drugs", params={"publication_status": "pending"}, headers=admin_headers
    )
    assert pending.json()["total"] == 1
    assert pending.json()["items"][0]["id"] == 2

    for environment in ("staging", "prod"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        public_list = await client.get(f"{API}/drugs", params={"page_size": 30})
        assert public_list.status_code == 200
        assert [d["id"] for d in public_list.json()["items"]] == [1]
        assert (await client.get(f"{API}/drugs/4", headers=user_headers)).status_code == 404
        assert (await client.get(f"{API}/drugs/1", headers=user_headers)).status_code == 200
        assert (
            await client.post(
                f"{API}/drugs/1/feedback", json={"content": FEEDBACK}, headers=user_headers
            )
        ).status_code == 503
        assert (
            await client.post(f"{API}/drugs/1/favorite", headers=user_headers)
        ).status_code == 200
        public_favorites = await client.get(f"{API}/favorites", headers=user_headers)
        assert {d["id"] for d in public_favorites.json()["items"]}.issubset({1})
