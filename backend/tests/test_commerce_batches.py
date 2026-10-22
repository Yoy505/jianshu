"""单位采购批次与权益认领：预览校验、确认发放、本人认领/拒绝、越权隔离、异常处理。"""

import pytest

from app.core.security import SCOPE_ORG
from tests.commerce_helpers import API, admin_headers, make_package, user_headers


async def _make_org_with_account(client, admin) -> tuple[int, str]:
    resp = await client.post(
        f"{API}/admin/mp/orgs",
        json={"name": "杏林示范单位", "contact_name": "采购负责人", "contact_phone": "13900000002"},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    org_id = resp.json()["id"]
    resp = await client.post(
        f"{API}/admin/mp/orgs/{org_id}/accounts",
        json={"username": "demo_org_01", "password": "Org-Pass-2026!x"},
        headers=admin,
    )
    assert resp.status_code == 201
    return org_id, "demo_org_01"


async def _preview(client, admin, org_id, made, rows, quantity=10):
    return await client.post(
        f"{API}/admin/mp/batches/preview",
        json={
            "org_id": org_id,
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "quantity": quantity,
            "file_name": "名单.csv",
            "rows": rows,
        },
        headers=admin,
    )


@pytest.mark.asyncio
async def test_batch_preview_validation(client):
    """预览逐行判定：格式错误 / 批内重复 / 跨批次重复 / 超量；未确认无权益。"""
    admin = await admin_headers(client)
    org_id, _ = await _make_org_with_account(client, admin)
    made = await make_package(client, admin)

    rows = [
        "13800000001",       # valid（种子用户手机号）
        "13800000001",       # 批内重复
        "12345",             # 格式错误
        "+8613900000003",    # 规范化后 valid
        "13900000003",       # 与上一行重复
    ]
    resp = await _preview(client, admin, org_id, made, rows, quantity=2)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["summary"] == {
        "total": 5, "valid": 2, "duplicate": 2, "format_error": 1, "over_limit": 0
    }
    statuses = {r["row_no"]: r["status"] for r in data["rows"]}
    assert statuses == {1: "valid", 2: "duplicate", 3: "format_error", 4: "valid", 5: "duplicate"}
    # 手机号掩码
    assert all("****" in r["phone"] for r in data["rows"] if r["status"] != "format_error")

    batch_id = data["batch"]["id"]
    # 未确认：无权益
    resp = await client.get(f"{API}/mp/entitlements?status=pending", headers=user_headers())
    assert resp.json()["total"] == 0

    # 超量：quantity=1 时第二行 valid 会变 over_limit
    resp = await _preview(client, admin, org_id, made, ["13800000001", "13900000003"], quantity=1)
    assert resp.status_code == 201
    statuses = {r["row_no"]: r["status"] for r in resp.json()["rows"]}
    assert statuses == {1: "valid", 2: "over_limit"}
    await client.post(f"{API}/admin/mp/batches/{resp.json()['batch']['id']}/cancel", headers=admin)


@pytest.mark.asyncio
async def test_batch_confirm_claim_reject_flow(client):
    """确认 → 每个有效行一条 pending 权益；本人认领 active；拒绝后可再认领。"""
    admin = await admin_headers(client)
    org_id, org_username = await _make_org_with_account(client, admin)
    made = await make_package(client, admin)

    rows = ["13800000001", "13911112222", "13933334444"]
    resp = await _preview(client, admin, org_id, made, rows, quantity=10)
    batch_id = resp.json()["batch"]["id"]

    # editor 只能预览，不能确认
    editor = await admin_headers(client, role="editor")
    resp = await client.post(f"{API}/admin/mp/batches/{batch_id}/confirm", headers=editor)
    assert resp.status_code == 403

    resp = await client.post(f"{API}/admin/mp/batches/{batch_id}/confirm", headers=admin)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"
    assert resp.json()["issued_count"] == 3

    # 重复确认 409
    resp = await client.post(f"{API}/admin/mp/batches/{batch_id}/confirm", headers=admin)
    assert resp.status_code == 409

    # 本人（13800000001）可见待领取权益并认领
    resp = await client.get(f"{API}/mp/entitlements?status=pending", headers=user_headers())
    pendings = resp.json()["items"]
    assert len(pendings) == 1
    ent = pendings[0]
    assert ent["source_type"] == "batch"
    assert ent["org_name"] == "杏林示范单位"

    resp = await client.post(f"{API}/mp/entitlements/{ent['id']}/claim", headers=user_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    assert resp.json()["valid_until"] is not None
    # 重复认领 → 200 同一结果
    resp = await client.post(f"{API}/mp/entitlements/{ent['id']}/claim", headers=user_headers())
    assert resp.status_code == 200 and resp.json()["status"] == "active"

    # 匹配失败的用户看不到也领不到（不泄露存在性）
    resp = await client.post(f"{API}/wx/login", json={"code": "dev-code"})
    other = {"Authorization": f"Bearer {resp.json()['token']}"}
    resp = await client.get(f"{API}/mp/entitlements/{ent['id']}", headers=other)
    assert resp.status_code == 404

    # 拒绝流程：同单位同版本同号只允许一份，换单位再开一批只含本人手机号
    resp = await client.post(
        f"{API}/admin/mp/orgs", json={"name": "杏林示范单位二号"}, headers=admin
    )
    org2_id = resp.json()["id"]
    resp = await _preview(client, admin, org2_id, made, ["13800000001"], quantity=5)
    resp = await client.post(
        f"{API}/admin/mp/batches/{resp.json()['batch']['id']}/confirm", headers=admin
    )
    assert resp.status_code == 200
    resp = await client.get(f"{API}/mp/entitlements?status=pending", headers=user_headers())
    ent2 = resp.json()["items"][0]
    resp = await client.post(f"{API}/mp/entitlements/{ent2['id']}/reject", headers=user_headers())
    assert resp.status_code == 200 and resp.json()["status"] == "rejected"
    # 重复拒绝 200；已拒绝可再认领
    resp = await client.post(f"{API}/mp/entitlements/{ent2['id']}/reject", headers=user_headers())
    assert resp.status_code == 200
    resp = await client.post(f"{API}/mp/entitlements/{ent2['id']}/claim", headers=user_headers())
    assert resp.status_code == 200 and resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_org_account_isolation(client):
    """单位账号：登录 → 只读本单位批次汇总，无手机号字段；user/admin token 打 /org 一律 401。"""
    admin = await admin_headers(client)
    org_id, _ = await _make_org_with_account(client, admin)
    made = await make_package(client, admin)
    resp = await _preview(client, admin, org_id, made, ["13800000001"], quantity=1)
    batch_id = resp.json()["batch"]["id"]
    await client.post(f"{API}/admin/mp/batches/{batch_id}/confirm", headers=admin)

    # 另一个单位（不应可见）
    resp = await client.post(
        f"{API}/admin/mp/orgs", json={"name": "第二单位"}, headers=admin
    )
    other_org_id = resp.json()["id"]

    resp = await client.post(
        f"{API}/org/auth/login", json={"username": "demo_org_01", "password": "Org-Pass-2026!x"}
    )
    assert resp.status_code == 200, resp.text
    org_token = resp.json()["access_token"]
    org_headers = {"Authorization": f"Bearer {org_token}"}

    resp = await client.get(f"{API}/org/me", headers=org_headers)
    assert resp.json()["org"]["name"] == "杏林示范单位"

    resp = await client.get(f"{API}/org/batches", headers=org_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    body = resp.text
    assert "13800000001" not in body and "phone" not in body  # 无任何个人信息
    assert items[0]["issued_count"] == 1
    assert items[0]["claimed_count"] == 0

    # 非本单位批次 404
    resp = await client.get(f"{API}/org/batches/{other_org_id + 1000}", headers=org_headers)
    assert resp.status_code == 404

    # 错误口令 401
    resp = await client.post(
        f"{API}/org/auth/login", json={"username": "demo_org_01", "password": "wrong-password"}
    )
    assert resp.status_code == 401

    # user token 访问 org 端点 401；org token 访问 mp 端点 401
    resp = await client.get(f"{API}/org/batches", headers=user_headers())
    assert resp.status_code == 401
    resp = await client.get(f"{API}/mp/points", headers=org_headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_entitlement_void_and_reassign(client):
    """权益异常处理：错号改派（pending/rejected）；作废留痕。"""
    admin = await admin_headers(client)
    org_id, _ = await _make_org_with_account(client, admin)
    made = await make_package(client, admin)
    resp = await _preview(client, admin, org_id, made, ["13900009999"], quantity=1)
    batch_id = resp.json()["batch"]["id"]
    await client.post(f"{API}/admin/mp/batches/{batch_id}/confirm", headers=admin)

    resp = await client.get(
        f"{API}/admin/mp/entitlements?status=pending", headers=admin
    )
    ent_id = resp.json()["items"][0]["id"]

    # 改号到种子用户手机号
    resp = await client.post(
        f"{API}/admin/mp/entitlements/{ent_id}/reassign",
        json={"phone": "13800000001", "reason": "名单错号修正"},
        headers=admin,
    )
    assert resp.status_code == 200
    assert resp.json()["phone"] == "138****0001"

    # 认领后不可改号
    await client.post(f"{API}/mp/entitlements/{ent_id}/claim", headers=user_headers())
    resp = await client.post(
        f"{API}/admin/mp/entitlements/{ent_id}/reassign",
        json={"phone": "13900001111", "reason": "再改"},
        headers=admin,
    )
    assert resp.status_code == 409

    # 作废
    resp = await client.post(
        f"{API}/admin/mp/entitlements/{ent_id}/void",
        json={"reason": "采购方撤回"},
        headers=admin,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "void"
    assert resp.json()["void_reason"] == "采购方撤回"

    # 已作废再作废 409
    resp = await client.post(
        f"{API}/admin/mp/entitlements/{ent_id}/void", json={"reason": "重复"}, headers=admin
    )
    assert resp.status_code == 409
