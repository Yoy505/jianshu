"""职业健康作品征集：真实 DOCX 解析、私有下载和失败关闭。"""

import io
import json
import subprocess
import uuid
import zipfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_redis
from app.core.occupational_upload_guard import (
    MAX_REQUEST_BYTES,
    OCCUPATIONAL_POST_PATH,
    OccupationalUploadGuard,
)
from app.main import app

API = "/api/v1"


def _docx(characters: int) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        # OOXML 必需关系部件：Word 要求 _rels/.rels，缺失会判定文件损坏
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{'职' * characters}</w:t></w:r></w:p></w:body></w:document>",
        )
    return data.getvalue()


def _form(work_type="article", client_submission_id=None) -> dict:
    return {
        "client_submission_id": str(client_submission_id or uuid.uuid4()),
        "work_type": work_type,
        "title": "职业健康科普作品",
        "topic": "办公人群肌肉骨骼健康",
        "author_name": "张三",
        "organization": "测试单位",
        "phone": "13800000088",
        "summary": "这是一篇面向公众的职业健康科普作品简介。",
        "consent_review_contact": "true",
    }


async def _admin_headers(client, username="admin", password="admin123") -> dict:
    result = await client.post(
        f"{API}/admin/auth/login", json={"username": username, "password": password}
    )
    assert result.status_code == 200
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


@pytest.mark.asyncio
async def test_closed_by_default_and_consent_required(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(tmp_path / "private"))
    meta = await client.get(f"{API}/h5/occupational-submissions/meta")
    assert meta.status_code == 200
    assert meta.json()["enabled"] is False
    assert meta.json()["accepts_other_topic"] is True

    files = {"file": ("work.docx", _docx(1000))}
    closed = await client.post(f"{API}/h5/occupational-submissions", data=_form(), files=files)
    assert closed.status_code == 503
    assert not (tmp_path / "private").exists()

    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    form = _form()
    form["consent_review_contact"] = "false"
    refused = await client.post(f"{API}/h5/occupational-submissions", data=form, files=files)
    assert refused.status_code == 422
    assert not (tmp_path / "private").exists()


@pytest.mark.asyncio
async def test_guard_rejects_declared_oversize_before_multipart_parsing(client, monkeypatch):
    too_large = {"Content-Length": str(MAX_REQUEST_BYTES + 1), "Content-Type": "application/octet-stream"}
    closed = await client.post(f"{API}/h5/occupational-submissions", content=b"x", headers=too_large)
    assert closed.status_code == 503
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    oversized = await client.post(f"{API}/h5/occupational-submissions", content=b"x", headers=too_large)
    assert oversized.status_code == 413
    assert "320 MiB" in oversized.json()["detail"]
    other_route = await client.post(f"{API}/h5/essays", content=b"x", headers=too_large)
    assert other_route.status_code != 413


@pytest.mark.asyncio
async def test_guard_does_not_read_closed_body_or_change_stream(monkeypatch):
    scope = {"type": "http", "method": "POST", "path": OCCUPATIONAL_POST_PATH, "headers": []}
    sent = []
    async def send(message):
        sent.append(message)
    async def must_not_receive():
        raise AssertionError("关闭时不应读取上传请求体")
    async def inner(scope, receive, send):
        body = await receive()
        assert body["body"] == b"streamed"
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    guard = OccupationalUploadGuard(inner)
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", False)
    await guard(scope, must_not_receive, send)
    assert sent[0]["status"] == 503

    sent.clear()
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    async def receive():
        return {"type": "http.request", "body": b"streamed", "more_body": False}
    await guard(scope, receive, send)
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_article_persists_then_admin_can_review_and_download(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    private_dir = tmp_path / "private"
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(private_dir))
    docx = _docx(1000)
    submit = await client.post(
        f"{API}/h5/occupational-submissions", data=_form(), files={"file": ("work.docx", docx)}
    )
    assert submit.status_code == 201, submit.text
    receipt = submit.json()
    assert set(receipt) == {"id", "receipt_no", "status", "submitted_at"}
    assert receipt["status"] == "submitted"
    assert receipt["receipt_no"].startswith("OCC-")
    assert len(list(private_dir.glob("*.docx"))) == 1

    unauth = await client.get(f"{API}/admin/occupational-submissions/{receipt['id']}/file")
    assert unauth.status_code == 401
    editor = await _admin_headers(client, "editor", "editor123")
    # 方案 B：editor 参与形式初审，可读列表
    assert (await client.get(f"{API}/admin/occupational-submissions", headers=editor)).status_code == 200

    admin = await _admin_headers(client)
    listing = await client.get(f"{API}/admin/occupational-submissions", headers=admin)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["phone_masked"] == "138****0088"
    assert "phone" not in listing.json()["items"][0]
    detail = await client.get(f"{API}/admin/occupational-submissions/{receipt['id']}", headers=admin)
    assert detail.status_code == 200
    assert detail.json()["word_count"] == 1000
    assert detail.json()["phone"] == "13800000088"
    meta = await client.get(f"{API}/h5/occupational-submissions/meta")
    assert detail.json()["consent_text"] == meta.json()["consent_text"]
    assert "stored_filename" not in detail.json()
    assert detail.json()["reviews"] == []
    assert detail.json()["lock"]["locked_by_admin_id"] is None
    downloaded = await client.get(f"{API}/admin/occupational-submissions/{receipt['id']}/file", headers=admin)
    assert downloaded.status_code == 200
    assert downloaded.content == docx
    assert downloaded.headers["cache-control"] == "no-store"

    # —— 两级评审：提交 → admin 领取初审 → 释放 → editor 领取并初审通过 → editor 终审被拒(403) → admin 终审通过 ——
    claim = await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/initial-review",
        headers=admin, json={"action": "claim"},
    )
    assert claim.status_code == 200
    assert claim.json()["status"] == "initial_reviewing"
    assert claim.json()["lock"]["locked_by_admin_id"] is not None
    assert claim.json()["lock"]["expired"] is False
    # 持锁期间其他初审员不能操作
    blocked = await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/initial-review",
        headers=editor, json={"action": "pass"},
    )
    assert blocked.status_code == 409
    # 非持锁人不能释放
    not_holder = await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/initial-review",
        headers=editor, json={"action": "release"},
    )
    assert not_holder.status_code == 409
    release = await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/initial-review",
        headers=admin, json={"action": "release"},
    )
    assert release.status_code == 200
    assert release.json()["status"] == "submitted"

    editor_claim = await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/initial-review",
        headers=editor, json={"action": "claim"},
    )
    assert editor_claim.status_code == 200
    initial_pass = await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/initial-review",
        headers=editor, json={"action": "pass", "comment": "材料齐全"},
    )
    assert initial_pass.status_code == 200
    assert initial_pass.json()["status"] == "pending_final"
    assert [r["stage"] for r in initial_pass.json()["reviews"]] == ["initial"]
    assert initial_pass.json()["lock"]["locked_by_admin_id"] is None

    # editor 无终审权限
    forbidden_final = await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/final-review",
        headers=editor, json={"action": "pass"},
    )
    assert forbidden_final.status_code == 403
    # 终审通过（初审=editor，终审=admin，双签成立）
    final = await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/final-review",
        headers=admin, json={"action": "pass", "comment": "内容合格", "score": 88},
    )
    assert final.status_code == 200
    assert final.json()["status"] == "accepted"
    assert [r["stage"] for r in final.json()["reviews"]] == ["initial", "final"]
    assert final.json()["reviews"][1]["reviewer_name"] == "系统管理员"
    assert final.json()["reviews"][1]["score"] == 88
    # 终态不可再审
    assert (await client.post(
        f"{API}/admin/occupational-submissions/{receipt['id']}/final-review",
        headers=admin, json={"action": "reject", "comment": "重复"},
    )).status_code == 409


@pytest.mark.asyncio
async def test_same_admin_cannot_double_sign_and_reject_requires_reason(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(tmp_path / "private"))
    submit = await client.post(
        f"{API}/h5/occupational-submissions", data=_form(), files={"file": ("work.docx", _docx(1000))}
    )
    assert submit.status_code == 201
    submission_id = submit.json()["id"]
    admin = await _admin_headers(client)

    # 未领取不能直接通过
    premature = await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/initial-review",
        headers=admin, json={"action": "pass"},
    )
    assert premature.status_code == 409
    assert (await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/final-review",
        headers=admin, json={"action": "pass"},
    )).status_code == 409

    assert (await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/initial-review",
        headers=admin, json={"action": "claim"},
    )).status_code == 200
    # 驳回必须填理由
    assert (await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/initial-review",
        headers=admin, json={"action": "reject"},
    )).status_code == 422
    # 同一账号初审通过后不能终审（双签制衡）
    assert (await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/initial-review",
        headers=admin, json={"action": "pass"},
    )).status_code == 200
    double_sign = await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/final-review",
        headers=admin, json={"action": "pass"},
    )
    assert double_sign.status_code == 409
    assert "同一账号" in double_sign.json()["detail"]


@pytest.mark.asyncio
async def test_initial_lock_expiry_allows_takeover(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(tmp_path / "private"))
    submit = await client.post(
        f"{API}/h5/occupational-submissions", data=_form(), files={"file": ("work.docx", _docx(1000))}
    )
    submission_id = submit.json()["id"]
    admin = await _admin_headers(client)
    editor = await _admin_headers(client, "editor", "editor123")
    assert (await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/initial-review",
        headers=admin, json={"action": "claim"},
    )).status_code == 200
    # 未过期时他人领取被拒
    assert (await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/initial-review",
        headers=editor, json={"action": "claim"},
    )).status_code == 409
    # 锁超时后可接管
    from datetime import timedelta
    import app.api.admin.occupational_submissions as admin_module
    monkeypatch.setattr(admin_module, "INITIAL_LOCK_TTL", timedelta(seconds=0))
    takeover = await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/initial-review",
        headers=editor, json={"action": "claim"},
    )
    assert takeover.status_code == 200
    assert takeover.json()["lock"]["expired"] is True
    passed = await client.post(
        f"{API}/admin/occupational-submissions/{submission_id}/initial-review",
        headers=editor, json={"action": "pass"},
    )
    assert passed.status_code == 200
    assert passed.json()["status"] == "pending_final"


@pytest.mark.asyncio
async def test_receipt_query_public_status_and_comment(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(tmp_path / "private"))
    submit = await client.post(
        f"{API}/h5/occupational-submissions", data=_form(), files={"file": ("work.docx", _docx(1000))}
    )
    receipt_no = submit.json()["receipt_no"]

    unknown = await client.get(f"{API}/h5/occupational-submissions/receipt/OCC-0000000000000000")
    assert unknown.status_code == 404
    bad_format = await client.get(f"{API}/h5/occupational-submissions/receipt/not-a-receipt")
    assert bad_format.status_code == 404

    early = await client.get(f"{API}/h5/occupational-submissions/receipt/{receipt_no}")
    assert early.status_code == 200
    assert early.json()["status"] == "submitted"
    assert early.json()["comment"] is None
    assert "phone" not in early.json()
    assert "organization" not in early.json()

    admin = await _admin_headers(client)
    await client.post(
        f"{API}/admin/occupational-submissions/{submit.json()['id']}/initial-review",
        headers=admin, json={"action": "claim"},
    )
    in_review = await client.get(f"{API}/h5/occupational-submissions/receipt/{receipt_no}")
    assert in_review.json()["status"] == "initial_reviewing"
    await client.post(
        f"{API}/admin/occupational-submissions/{submit.json()['id']}/initial-review",
        headers=admin, json={"action": "reject", "comment": "正文不足征集要求"},
    )
    rejected = await client.get(f"{API}/h5/occupational-submissions/receipt/{receipt_no}")
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["comment"] == "正文不足征集要求"
    assert rejected.json()["decided_at"] is not None


@pytest.mark.asyncio
async def test_idempotent_retry_and_changed_form_conflict(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    private_dir = tmp_path / "private"
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(private_dir))
    key = uuid.uuid4()
    form = _form(client_submission_id=key)
    docx = _docx(1000)
    first = await client.post(
        f"{API}/h5/occupational-submissions", data=form, files={"file": ("work.docx", docx)}
    )
    assert first.status_code == 201
    replay = await client.post(
        f"{API}/h5/occupational-submissions", data=form, files={"file": ("work.docx", docx)}
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert len(list(private_dir.glob("*.docx"))) == 1

    changed_file = await client.post(
        f"{API}/h5/occupational-submissions", data=form,
        files={"file": ("work.docx", _docx(1001))},
    )
    assert changed_file.status_code == 409
    assert "文件已变更" in changed_file.json()["detail"]
    assert len(list(private_dir.glob("*.docx"))) == 1

    changed = dict(form, title="不同标题")
    conflict = await client.post(
        f"{API}/h5/occupational-submissions", data=changed, files={"file": ("work.docx", docx)}
    )
    assert conflict.status_code == 409
    other_phone = dict(form, phone="13800000089")
    occupied = await client.post(
        f"{API}/h5/occupational-submissions", data=other_phone, files={"file": ("work.docx", docx)}
    )
    assert occupied.status_code == 409
    admin = await _admin_headers(client)
    listing = await client.get(f"{API}/admin/occupational-submissions", headers=admin)
    assert listing.json()["total"] == 1
    detail = await client.get(
        f"{API}/admin/occupational-submissions/{first.json()['id']}", headers=admin
    )
    assert detail.json()["client_submission_id"] == str(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_file, expected_status", [(True, 200), (False, 409)])
async def test_idempotent_retry_after_unique_key_race(
    client, tmp_path, monkeypatch, same_file, expected_status,
):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    private_dir = tmp_path / "private"
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(private_dir))
    form = _form(client_submission_id=uuid.uuid4())
    original_docx = _docx(1000)
    first = await client.post(
        f"{API}/h5/occupational-submissions", data=form,
        files={"file": ("work.docx", original_docx)},
    )
    assert first.status_code == 201

    # 模拟两次请求均在预检查时未查到记录，唯一键冲突后回查胜出的记录。
    original_execute = AsyncSession.execute
    precheck_missed = False

    class NoRow:
        def scalar_one_or_none(self):
            return None

    async def miss_precheck_once(self, statement, *args, **kwargs):
        nonlocal precheck_missed
        if not precheck_missed:
            precheck_missed = True
            return NoRow()
        return await original_execute(self, statement, *args, **kwargs)

    retried_docx = original_docx if same_file else _docx(1001)
    with monkeypatch.context() as patcher:
        patcher.setattr(AsyncSession, "execute", miss_precheck_once)
        retried = await client.post(
            f"{API}/h5/occupational-submissions", data=form,
            files={"file": ("work.docx", retried_docx)},
        )

    assert precheck_missed
    assert retried.status_code == expected_status, retried.text
    if same_file:
        assert retried.json() == first.json()
    else:
        assert "文件已变更" in retried.json()["detail"]
    assert len(list(private_dir.glob("*.docx"))) == 1
    assert next(private_dir.glob("*.docx")).read_bytes() == original_docx
    admin = await _admin_headers(client)
    listing = await client.get(f"{API}/admin/occupational-submissions", headers=admin)
    assert listing.json()["total"] == 1


@pytest.mark.asyncio
async def test_landline_contact_is_accepted_and_masked(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(tmp_path / "private"))
    form = _form()
    form["phone"] = "022-12345678-123"
    created = await client.post(
        f"{API}/h5/occupational-submissions", data=form,
        files={"file": ("work.docx", _docx(1000))},
    )
    assert created.status_code == 201, created.text
    admin = await _admin_headers(client)
    listing = await client.get(f"{API}/admin/occupational-submissions", headers=admin)
    assert listing.json()["items"][0]["phone_masked"] == "022-****5678-****"
    detail = await client.get(
        f"{API}/admin/occupational-submissions/{created.json()['id']}", headers=admin
    )
    assert detail.json()["phone"] == "022-12345678-123"


@pytest.mark.asyncio
async def test_docx_word_count_recorded_but_not_enforced(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    private_dir = tmp_path / "private"
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(private_dir))
    # 2026-10-08 起不限字数：短文（如报名表）也允许投稿，字数仅存档供评审参考
    short = await client.post(
        f"{API}/h5/occupational-submissions", data=_form(), files={"file": ("work.docx", _docx(334))}
    )
    assert short.status_code == 201, short.text
    admin = await _admin_headers(client)
    detail = await client.get(
        f"{API}/admin/occupational-submissions/{short.json()['id']}", headers=admin
    )
    assert detail.json()["word_count"] == 334
    fake = await client.post(
        f"{API}/h5/occupational-submissions", data=_form(), files={"file": ("work.docx", b"not a zip")}
    )
    assert fake.status_code == 400

    # 缺 _rels/.rels 的包：Word 打开会判定损坏，服务端必须拒收
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{'职' * 1200}</w:t></w:r></w:p></w:body></w:document>",
        )
    rejected = await client.post(
        f"{API}/h5/occupational-submissions",
        data=_form(), files={"file": ("work.docx", broken.getvalue())},
    )
    assert rejected.status_code == 400
    assert "无法解析 DOCX" in rejected.json()["detail"]
    # 仅成功那份落盘；两次拒收不残留文件
    stored = [p for p in private_dir.iterdir() if not p.name.startswith(".incoming-")]
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_video_uses_ffprobe_and_rejects_bad_metadata(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(tmp_path / "private"))
    video = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00" + b"video-data"
    def probe(*args, **kwargs):
        assert args[0][0] == settings.FFPROBE_BIN
        return subprocess.CompletedProcess(
            args[0], 0,
            json.dumps({"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "180.0"},
                        "streams": [{"codec_type": "video", "width": 1080, "height": 1920}]}), "",
        )
    monkeypatch.setattr("app.services.occupational_submission_service.subprocess.run", probe)
    created = await client.post(
        f"{API}/h5/occupational-submissions", data=_form("video"), files={"file": ("work.mp4", video)}
    )
    assert created.status_code == 201, created.text
    admin = await _admin_headers(client)
    detail = await client.get(
        f"{API}/admin/occupational-submissions/{created.json()['id']}", headers=admin
    )
    assert detail.json()["duration_seconds"] == 180.0
    assert detail.json()["video_width"] == 1080
    assert detail.json()["video_height"] == 1920


@pytest.mark.asyncio
async def test_video_duration_and_resolution_use_probe_results(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    private_dir = tmp_path / "private"
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(private_dir))
    video = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00" + b"video-data"

    def probe_with(duration, width, height):
        def run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args[0], 0,
                json.dumps({
                    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": str(duration)},
                    "streams": [{"codec_type": "video", "width": width, "height": height}],
                }), "",
            )
        return run

    monkeypatch.setattr(
        "app.services.occupational_submission_service.subprocess.run",
        probe_with(12, 1080, 1920),
    )
    # 2026-10-08 起不限时长：十几秒的短片也允许投稿，时长仅存档
    short = await client.post(
        f"{API}/h5/occupational-submissions", data=_form("video"), files={"file": ("work.mp4", video)}
    )
    assert short.status_code == 201, short.text

    monkeypatch.setattr(
        "app.services.occupational_submission_service.subprocess.run",
        probe_with(180, 720, 1280),
    )
    small = await client.post(
        f"{API}/h5/occupational-submissions", data=_form("video"), files={"file": ("work.mp4", video)}
    )
    assert small.status_code == 400
    assert "1080P" in small.json()["detail"]


@pytest.mark.asyncio
async def test_redis_and_ffprobe_unavailable_block_public_upload(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSIONS_ENABLED", True)
    private_dir = tmp_path / "private"
    monkeypatch.setattr(settings, "OCCUPATIONAL_SUBMISSION_DIR", str(private_dir))
    class DownRedis:
        async def incr(self, key):
            raise ConnectionError("redis down")
    original_redis_override = app.dependency_overrides[get_redis]
    app.dependency_overrides[get_redis] = lambda: DownRedis()
    down = await client.post(
        f"{API}/h5/occupational-submissions", data=_form(), files={"file": ("work.docx", _docx(1000))}
    )
    assert down.status_code == 503
    assert not private_dir.exists()

    app.dependency_overrides[get_redis] = original_redis_override
    def missing(*args, **kwargs):
        raise FileNotFoundError("ffprobe")
    monkeypatch.setattr("app.services.occupational_submission_service.subprocess.run", missing)
    video = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00" + b"video-data"
    unavailable = await client.post(
        f"{API}/h5/occupational-submissions", data=_form("video"), files={"file": ("work.mp4", video)}
    )
    assert unavailable.status_code == 503
    assert "视频校验服务" in unavailable.json()["detail"]
    assert not list(private_dir.iterdir())
