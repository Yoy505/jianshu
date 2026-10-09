"""后台导出从任务创建到私有文件下载的权限与存储验证。"""

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.api.admin import essays as admin_essays
from app.api.admin import tasks as admin_tasks
from app.core.config import settings
from app.core.security import SCOPE_USER, TOKEN_TYPE_ACCESS, create_token
from app.main import app
from app.services.export_storage import private_export_dir
from app.workers import tasks as worker_tasks


def test_private_export_dir_cannot_be_under_public_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "EXPORT_DIR", str(tmp_path / "uploads" / "exports"))
    with pytest.raises(RuntimeError, match="不得位于公开 UPLOAD_DIR"):
        private_export_dir()


@pytest.mark.asyncio
async def test_export_task_status_and_authenticated_download(client, tmp_path, monkeypatch):
    public_dir = tmp_path / "uploads"
    private_dir = tmp_path / "private_exports"
    public_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(public_dir))
    monkeypatch.setattr(settings, "EXPORT_DIR", str(private_dir))

    # 让全局应用的静态挂载在本测试中使用隔离目录。
    public_mount = next(route for route in app.routes if getattr(route, "path", None) == "/uploads")
    monkeypatch.setattr(public_mount.app, "directory", str(public_dir))
    monkeypatch.setattr(public_mount.app, "all_directories", [str(public_dir)])

    async def fake_rows(contest_id, status):
        assert contest_id is None
        assert status is None
        return [(1, "测试作品", "作者甲", "测试活动", "approved", 90, 2, 5, "2026-09-23 12:00:00")]

    monkeypatch.setattr(worker_tasks, "_fetch_export_rows", fake_rows)
    result = await asyncio.to_thread(worker_tasks.export_essays_xlsx.run)
    filename = Path(result["url"]).name
    assert (private_dir / filename).is_file()
    assert not (public_dir / "exports" / filename).exists()
    assert (private_dir / filename).stat().st_mode & 0o777 == 0o600

    # 已部署旧版本的文件即使仍在公开目录，也不能再被静态路由访问。
    legacy_dir = public_dir / "exports"
    legacy_dir.mkdir()
    (legacy_dir / "old.xlsx").write_bytes(b"legacy private data")
    (public_dir / "cover.txt").write_text("public", encoding="utf-8")
    assert (await client.get("/uploads/exports/old.xlsx")).status_code == 404
    assert (await client.head("/uploads/exports/old.xlsx")).status_code == 404
    assert (await client.get("/uploads/%65xports/old.xlsx")).status_code == 404
    assert (await client.get("/uploads/cover.txt")).status_code == 200

    task_id = "task-export-1"
    monkeypatch.setattr(admin_essays.export_essays_xlsx, "delay", lambda *args: SimpleNamespace(id=task_id))
    monkeypatch.setattr(
        admin_tasks,
        "AsyncResult",
        lambda incoming_id, app: SimpleNamespace(state="SUCCESS", result=result),
    )

    login = await client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin123"}
    )
    admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    user_headers = {"Authorization": f"Bearer {create_token(1, SCOPE_USER, TOKEN_TYPE_ACCESS)}"}

    assert (await client.post("/api/v1/admin/essays/export", json={})).status_code == 401
    created = await client.post("/api/v1/admin/essays/export", json={}, headers=admin_headers)
    assert created.status_code == 202
    assert created.json()["task_id"] == task_id

    assert (await client.get(f"/api/v1/admin/tasks/{task_id}")).status_code == 401
    status = await client.get(f"/api/v1/admin/tasks/{task_id}", headers=admin_headers)
    assert status.status_code == 200
    assert status.json()["result"] == result

    download_path = "/api/v1" + status.json()["result"]["url"]
    assert (await client.get(download_path)).status_code == 401
    assert (await client.get(download_path, headers=user_headers)).status_code == 401
    assert (await client.get("/uploads/exports/" + filename)).status_code == 404
    download = await client.get(download_path, headers=admin_headers)
    assert download.status_code == 200
    assert download.headers["cache-control"] == "no-store"
    assert "attachment" in download.headers["content-disposition"]
    workbook = load_workbook(BytesIO(download.content), read_only=True)
    assert workbook.active["B2"].value == "测试作品"
    workbook.close()

    assert (await client.get("/api/v1/admin/essays/exports/old.xlsx", headers=admin_headers)).status_code == 404
