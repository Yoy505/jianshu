"""说明书后台元数据只读查询：鉴权、筛选、分页及私有文件隔离。"""

from datetime import date

import pytest

from app.core.deps import get_db
from app.main import app
from app.models import MpDrug, MpDrugLeaflet


@pytest.mark.asyncio
async def test_admin_leaflet_metadata_is_filtered_and_never_exposes_private_file(client):
    login = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    drugs = await client.get("/api/v1/admin/mp/drugs?page_size=1", headers=headers)
    drug_id = drugs.json()["items"][0]["id"]

    session_generator = app.dependency_overrides[get_db]()
    session = await anext(session_generator)
    try:
        drug = await session.get(MpDrug, drug_id)
        drug.approval_no = "国药准字H37020550"
        pending = MpDrugLeaflet(
            drug_id=drug_id,
            approval_no=drug.approval_no,
            source_name="授权来源甲",
            source_document_id="DOC-001",
            source_url="https://private.example.invalid/download/original.pdf",
            authorization_ref="internal-evaluation-only:CDE-001",
            identity_check_method="pypdf_text_match",
            document_date=date(2025, 1, 2),
            document_version="V1",
            source_checked_at=date(2026, 9, 24),
            original_filename="secret-original.pdf",
            file_size=12345,
            file_sha256="a" * 64,
            stored_filename="secret-private-stored.pdf",
            manifest_sha256="b" * 64,
            status="pending",
        )
        withdrawn = MpDrugLeaflet(
            drug_id=drug_id,
            approval_no=drug.approval_no,
            source_name="授权来源乙",
            source_document_id="DOC-002",
            source_url=None,
            authorization_ref="AUTH-002",
            identity_check_method=None,
            document_date=None,
            document_version=None,
            source_checked_at=date(2026, 9, 24),
            original_filename="other-original.pdf",
            file_size=400,
            file_sha256="c" * 64,
            stored_filename="other-private-stored.pdf",
            manifest_sha256="d" * 64,
            status="withdrawn",
        )
        session.add_all([pending, withdrawn])
        await session.commit()
        pending_id = pending.id
    finally:
        await session_generator.aclose()

    list_path = "/api/v1/admin/mp/drug-leaflets"
    assert (await client.get(list_path)).status_code == 401
    assert (await client.get(f"{list_path}/{pending_id}")).status_code == 401
    assert (await client.get(f"{list_path}?status=invalid", headers=headers)).status_code == 400

    response = await client.get(
        f"{list_path}?status=pending&approval_no=国药准字H37020550&page_size=1",
        headers=headers,
    )
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1 and len(page["items"]) == 1
    item = page["items"][0]
    assert item["id"] == pending_id
    assert item["drug_id"] == drug_id
    assert item["drug_common_name"] == drugs.json()["items"][0]["common_name"]
    assert item["source_document_id"] == "DOC-001"
    assert item["authorization_ref"] == "internal-evaluation-only:CDE-001"
    assert item["identity_check_method"] == "pypdf_text_match"
    assert item["file_sha256"] == "a" * 64
    assert item["manifest_sha256"] == "b" * 64
    for forbidden in ("stored_filename", "original_filename", "source_url", "download_url"):
        assert forbidden not in item

    no_match = await client.get(
        f"{list_path}?approval_no=国药准字H00000000", headers=headers
    )
    assert no_match.status_code == 200 and no_match.json()["total"] == 0

    all_first_page = await client.get(f"{list_path}?page_size=1", headers=headers)
    assert all_first_page.status_code == 200
    assert all_first_page.json()["total"] == 2
    assert len(all_first_page.json()["items"]) == 1
    withdrawn_page = await client.get(f"{list_path}?status=withdrawn", headers=headers)
    assert withdrawn_page.status_code == 200
    assert withdrawn_page.json()["total"] == 1
    assert withdrawn_page.json()["items"][0]["status"] == "withdrawn"
    assert withdrawn_page.json()["items"][0]["identity_check_method"] is None

    detail = await client.get(f"{list_path}/{pending_id}", headers=headers)
    assert detail.status_code == 200 and detail.json() == item
    serialized = detail.text
    assert "secret-original.pdf" not in serialized
    assert "secret-private-stored.pdf" not in serialized
    assert "private.example.invalid" not in serialized
    assert (await client.get(f"{list_path}/999999", headers=headers)).status_code == 404
