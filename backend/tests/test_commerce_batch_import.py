"""I2 真实名单文件导入回归（2026-10-22）：解析、原件私存、摘要口径、鉴权下载。"""

import hashlib

import pytest

from tests.commerce_helpers import API, admin_headers, make_package


@pytest.mark.asyncio
async def test_file_preview_parses_stores_and_downloads(client):
    admin = await admin_headers(client)
    resp = await client.post(f"{API}/admin/mp/orgs", json={"name": "I2 单位"}, headers=admin)
    org_id = resp.json()["id"]
    made = await make_package(client, admin)

    content = "手机号,备注\n13800000001,张\n+86 13900002222,\n\n13933334444\n".encode()
    resp = await client.post(
        f"{API}/admin/mp/batches/preview-file",
        data={
            "org_id": org_id,
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "quantity": 10,
        },
        files={"file": ("名单.csv", content, "text/csv")},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    # 表头跳过、+86 规范化、空行忽略
    statuses = {r["row_no"]: r["status"] for r in data["rows"]}
    assert statuses == {1: "valid", 2: "valid", 3: "valid"}
    batch = data["batch"]
    # 原件字节摘要（非规范化摘要）
    assert batch["file_sha256"] == hashlib.sha256(content).hexdigest()

    # 原件鉴权下载：内容一致 + 禁缓存；editor 同权限组可下载，跨用户域无权限者 403
    resp = await client.get(f"{API}/admin/mp/batches/{batch['id']}/file", headers=admin)
    assert resp.status_code == 200
    assert resp.content == content
    assert resp.headers["cache-control"] == "no-store"

    # 确认只消费服务端预览数据
    resp = await client.post(f"{API}/admin/mp/batches/{batch['id']}/confirm", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["issued_count"] == 3


@pytest.mark.asyncio
async def test_file_preview_rejects_bad_encoding_and_oversize(client):
    admin = await admin_headers(client)
    resp = await client.post(f"{API}/admin/mp/orgs", json={"name": "I2 编码单位"}, headers=admin)
    org_id = resp.json()["id"]
    made = await make_package(client, admin)

    # GBK 编码 → 422 明确提示
    gbk = "手机号\n13800000001,张三\n".encode("gbk")
    resp = await client.post(
        f"{API}/admin/mp/batches/preview-file",
        data={"org_id": org_id, "package_id": made["package_id"], "version_id": made["version_id"], "quantity": 5},
        files={"file": ("名单.txt", gbk, "text/plain")},
        headers=admin,
    )
    assert resp.status_code == 422
    assert "UTF-8" in resp.json()["detail"]

    # 超过 2 MiB → 413
    big = ("13800000001\n" * 200_000).encode()
    assert len(big) > 2 * 1024 * 1024
    resp = await client.post(
        f"{API}/admin/mp/batches/preview-file",
        data={"org_id": org_id, "package_id": made["package_id"], "version_id": made["version_id"], "quantity": 5},
        files={"file": ("big.txt", big, "text/plain")},
        headers=admin,
    )
    assert resp.status_code == 413

    # finance 无批次权限 → 403
    from tests.commerce_helpers import admin_headers as _ah

    resp = await client.post(
        f"{API}/admin/mp/batches/preview-file",
        data={"org_id": org_id, "package_id": made["package_id"], "version_id": made["version_id"], "quantity": 5},
        files={"file": ("x.txt", b"13800000001\n", "text/plain")},
        headers=await _ah(client, role="finance"),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_json_preview_records_normalized_digest(client):
    """JSON 预览无原件：file_sha256 即规范化摘要，且不提供原件下载。"""
    admin = await admin_headers(client)
    resp = await client.post(f"{API}/admin/mp/orgs", json={"name": "I2 JSON 单位"}, headers=admin)
    org_id = resp.json()["id"]
    made = await make_package(client, admin)
    resp = await client.post(
        f"{API}/admin/mp/batches/preview",
        json={
            "org_id": org_id,
            "package_id": made["package_id"],
            "version_id": made["version_id"],
            "quantity": 5,
            "file_name": "paste.txt",
            "rows": ["13800000001"],
        },
        headers=admin,
    )
    assert resp.status_code == 201
    batch_id = resp.json()["batch"]["id"]
    assert len(resp.json()["batch"]["file_sha256"]) == 64
    resp = await client.get(f"{API}/admin/mp/batches/{batch_id}/file", headers=admin)
    assert resp.status_code == 404
