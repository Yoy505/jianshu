"""本地说明书暂存：严格清单、私有原件、幂等批次与公开隔离。"""

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core import storage_paths
from app.models import MpDrug, MpDrugLeaflet
from scripts.import_drug_leaflets import (
    LeafletManifestError,
    audit_orphans,
    import_records,
    load_manifest,
    private_leaflet_dir,
)


def _pdf(
    path: Path, *, approval_no: str = "TEST-001", name: str = "Alpha Drug",
    variant: str = "first", blank: bool = False,
) -> str:
    """生成可被 pypdf 提取的最小 PDF；空白页用于人工核对分支。"""
    text = (
        "" if blank else
        f"{approval_no} {name} {variant} " + "Local identity verification sample text. " * 5
    )
    content = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii")
    parts = [b"%PDF-1.4\n"]
    offsets = [0]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    for number, obj in enumerate(objects, start=1):
        offsets.append(sum(map(len, parts)))
        parts.append(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_offset = sum(map(len, parts))
    parts.append(b"xref\n0 6\n0000000000 65535 f \n")
    parts.extend(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    parts.append(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref_offset).encode() + b"\n%%EOF\n")
    body = b"".join(parts)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _manifest(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "dataset_name": "仅供测试的说明书清单",
        "records": rows,
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _row(approval_no: str, file_name: str, sha256: str, *, document_id: str = "doc-1") -> dict:
    return {
        "approval_no": approval_no,
        "pdf_path": file_name,
        "source_name": "测试提供方",
        "source_document_id": document_id,
        "source_url": "https://example.org/leaflet",
        "authorization_ref": "internal-evaluation-only:test-case",
        "document_date": None,
        "document_version": None,
        "source_checked_at": "2026-09-24",
        "expected_sha256": sha256,
    }


def _drug(approval_no: str, name: str, status: str = "pending") -> MpDrug:
    return MpDrug(
        approval_no=approval_no,
        common_name=name,
        form="片剂",
        manufacturer="原有生产企业",
        tags=[],
        indication="",
        usage_note="",
        contraindication="",
        science="",
        initial="C",
        publication_status=status,
    )


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(MpDrug.__table__.create)
        await connection.run_sync(MpDrugLeaflet.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_manifest_preflight_rejects_unverified_content_and_bad_paths(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    digest = _pdf(source_root / "one.pdf")
    row = _row("TEST-001", "one.pdf", digest)
    assert len(load_manifest(_manifest(tmp_path, [row]), source_root)) == 1

    bad = {**row, "indication": "不能夹带未经审核的适应症"}
    with pytest.raises(LeafletManifestError, match="字段不符合"):
        load_manifest(_manifest(tmp_path, [bad]), source_root)

    bad = {**row, "pdf_path": "../one.pdf"}
    with pytest.raises(LeafletManifestError, match="相对 PDF 路径"):
        load_manifest(_manifest(tmp_path, [bad]), source_root)

    bad = {**row, "expected_sha256": "0" * 64}
    with pytest.raises(LeafletManifestError, match="SHA-256"):
        load_manifest(_manifest(tmp_path, [bad]), source_root)

    bad = {**row, "expected_sha256": None}
    with pytest.raises(LeafletManifestError, match="expected_sha256"):
        load_manifest(_manifest(tmp_path, [bad]), source_root)

    bad = {**row, "authorization_ref": "待确认"}
    with pytest.raises(LeafletManifestError, match="使用依据"):
        load_manifest(_manifest(tmp_path, [bad]), source_root)

    (source_root / "one.pdf").write_bytes(b"not a PDF")
    with pytest.raises(LeafletManifestError, match="PDF 大小|不是 PDF"):
        load_manifest(_manifest(tmp_path, [row]), source_root)


def test_private_leaflet_store_cannot_be_inside_public_uploads(tmp_path, monkeypatch):
    public = tmp_path / "uploads"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(public))
    monkeypatch.setattr(settings, "DRUG_LEAFLET_DIR", str(public / "leaflets"))
    with pytest.raises(RuntimeError, match="不能位于公开 UPLOAD_DIR"):
        private_leaflet_dir(create=True)


def test_source_root_cannot_be_inside_public_uploads(tmp_path, monkeypatch):
    public = tmp_path / "uploads"
    public.mkdir()
    digest = _pdf(public / "one.pdf")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(public))
    with pytest.raises(LeafletManifestError, match="source-root 不能位于公开"):
        load_manifest(_manifest(tmp_path, [_row("TEST-001", "one.pdf", digest)]), public)


def test_source_root_guard_uses_backend_path_when_cli_cwd_differs(tmp_path, monkeypatch):
    backend_root = tmp_path / "backend"
    public = backend_root / "uploads"
    public.mkdir(parents=True)
    digest = _pdf(public / "one.pdf")
    monkeypatch.setattr(storage_paths, "BACKEND_DIR", backend_root)
    monkeypatch.setattr(settings, "UPLOAD_DIR", "./uploads")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LeafletManifestError, match="source-root 不能位于公开"):
        load_manifest(_manifest(tmp_path, [_row("TEST-001", "one.pdf", digest)]), public)


@pytest.mark.asyncio
async def test_dry_run_batch_resume_and_immutable_pending_versions(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    first_hash = _pdf(source_root / "first.pdf")
    second_hash = _pdf(source_root / "second.pdf", approval_no="TEST-002", name="Beta Drug", variant="second")
    manifest_path = _manifest(tmp_path, [
        _row("TEST-001", "first.pdf", first_hash),
        _row("TEST-002", "second.pdf", second_hash, document_id="doc-2"),
    ])
    records = load_manifest(manifest_path, source_root)
    private_root = tmp_path / "private"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "DRUG_LEAFLET_DIR", str(private_root))

    engine, factory = await _database()
    try:
        async with factory() as session:
            session.add_all([_drug("TEST-001", "Alpha Drug"), _drug("TEST-002", "Beta Drug")])
            await session.commit()

        dry = await import_records(factory, records, batch_size=1)
        assert dry.count("insert") == 2
        assert not private_root.exists()
        async with factory() as session:
            assert (await session.scalars(select(MpDrugLeaflet))).all() == []

        first = await import_records(factory, records[:1], apply=True, batch_size=1)
        assert first.count("insert") == 1
        resumed = await import_records(factory, records, apply=True, batch_size=1)
        assert resumed.count("unchanged") == 1
        assert resumed.count("insert") == 1
        assert (private_root / f"{first_hash}.pdf").read_bytes() == (source_root / "first.pdf").read_bytes()
        assert (private_root / f"{second_hash}.pdf").read_bytes() == (source_root / "second.pdf").read_bytes()

        async with factory() as session:
            leaflets = (await session.scalars(select(MpDrugLeaflet).order_by(MpDrugLeaflet.id))).all()
            drugs = (await session.scalars(select(MpDrug).order_by(MpDrug.id))).all()
            assert len(leaflets) == 2
            assert {row.status for row in leaflets} == {"pending"}
            assert {row.identity_check_method for row in leaflets} == {"pypdf_text_match"}
            assert leaflets[0].authorization_ref == "internal-evaluation-only:test-case"
            assert leaflets[0].manifest_sha256 == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            assert [(drug.common_name, drug.manufacturer, drug.publication_status) for drug in drugs] == [
                ("Alpha Drug", "原有生产企业", "pending"),
                ("Beta Drug", "原有生产企业", "pending"),
            ]

        revised_hash = _pdf(source_root / "revised.pdf", variant="revised")
        revision = load_manifest(_manifest(tmp_path, [
            _row("TEST-001", "revised.pdf", revised_hash, document_id="doc-1")
        ]), source_root)
        revised = await import_records(factory, revision, apply=True)
        assert revised.count("insert") == 1
        async with factory() as session:
            all_leaflets = (await session.scalars(select(MpDrugLeaflet))).all()
            assert len(all_leaflets) == 3
            assert {row.file_sha256 for row in all_leaflets} == {
                first_hash, second_hash, revised_hash,
            }

        async with factory() as session:
            legacy = await session.scalar(select(MpDrugLeaflet).where(MpDrugLeaflet.file_sha256 == first_hash))
            legacy.identity_check_method = None
            await session.commit()
        backfill = await import_records(factory, records[:1], apply=True)
        assert backfill.count("identity_backfill") == 1
        async with factory() as session:
            legacy = await session.scalar(select(MpDrugLeaflet).where(MpDrugLeaflet.file_sha256 == first_hash))
            assert legacy.identity_check_method == "pypdf_text_match"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_or_demo_drug_never_stages_pdf(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    digest = _pdf(source_root / "one.pdf")
    records = load_manifest(_manifest(tmp_path, [
        _row("TEST-DEMO", "one.pdf", digest),
        _row("TEST-MISSING", "one.pdf", digest, document_id="doc-2"),
    ]), source_root)
    private_root = tmp_path / "private"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "DRUG_LEAFLET_DIR", str(private_root))
    engine, factory = await _database()
    try:
        async with factory() as session:
            session.add(_drug("TEST-DEMO", "演示药", "demo"))
            await session.commit()
        report = await import_records(factory, records, apply=True, batch_size=1)
        assert report.count("demo_blocked") == 1
        assert report.count("unmatched") == 1
        assert list(private_root.glob("*.pdf")) == []
        async with factory() as session:
            assert (await session.scalars(select(MpDrugLeaflet))).all() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wrong_drug_pdf_and_unreadable_page_require_review(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    wrong_hash = _pdf(source_root / "wrong.pdf", approval_no="TEST-001", name="Alpha Drug")
    blank_hash = _pdf(source_root / "blank.pdf", blank=True)
    records = load_manifest(_manifest(tmp_path, [
        _row("TEST-002", "wrong.pdf", wrong_hash),
        _row("TEST-003", "blank.pdf", blank_hash, document_id="doc-3"),
    ]), source_root)
    private_root = tmp_path / "private"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "DRUG_LEAFLET_DIR", str(private_root))
    engine, factory = await _database()
    try:
        async with factory() as session:
            session.add_all([_drug("TEST-002", "Beta Drug"), _drug("TEST-003", "Gamma Drug")])
            await session.commit()
        report = await import_records(factory, records, apply=True)
        assert report.count("identity_mismatch") == 1
        assert report.count("manual_review") == 1
        assert list(private_root.glob("*.pdf")) == []
        async with factory() as session:
            assert (await session.scalars(select(MpDrugLeaflet))).all() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_commit_recovers_only_new_unreferenced_pdf(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    digest = _pdf(source_root / "one.pdf")
    records = load_manifest(_manifest(tmp_path, [_row("TEST-001", "one.pdf", digest)]), source_root)
    private_root = tmp_path / "private"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "DRUG_LEAFLET_DIR", str(private_root))
    engine, factory = await _database()
    try:
        async with factory() as session:
            session.add(_drug("TEST-001", "Alpha Drug"))
            await session.commit()

        async def fail_commit(_self):
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(AsyncSession, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected commit failure"):
            await import_records(factory, records, apply=True)
        assert list(private_root.glob("*.pdf")) == []
        assert await audit_orphans(factory) == ()
        async with factory() as session:
            assert (await session.scalars(select(MpDrugLeaflet))).all() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_orphan_audit_preserves_shared_hash_with_database_reference(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    digest = _pdf(source_root / "shared.pdf", approval_no="TEST-001 TEST-002", name="Alpha Drug Beta Drug")
    first = load_manifest(_manifest(tmp_path, [_row("TEST-001", "shared.pdf", digest)]), source_root)
    private_root = tmp_path / "private"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "DRUG_LEAFLET_DIR", str(private_root))
    engine, factory = await _database()
    try:
        async with factory() as session:
            session.add_all([_drug("TEST-001", "Alpha Drug"), _drug("TEST-002", "Beta Drug")])
            await session.commit()
        assert (await import_records(factory, first, apply=True)).count("insert") == 1
        # 同一内容哈希即使将来由另一个药品引用，也只有一个私有原件。
        second = load_manifest(_manifest(tmp_path, [
            _row("TEST-002", "shared.pdf", digest, document_id="doc-2")
        ]), source_root)
        assert (await import_records(factory, second, apply=True)).count("insert") == 1
        assert len(list(private_root.glob("*.pdf"))) == 1

        orphan_hash = _pdf(private_root / "temporary-source.pdf", variant="orphan")
        (private_root / f"{orphan_hash}.pdf").write_bytes(
            (private_root / "temporary-source.pdf").read_bytes()
        )
        (private_root / "temporary-source.pdf").unlink()
        assert await audit_orphans(factory) == (f"{orphan_hash}.pdf",)
        assert await audit_orphans(factory, remove=True) == (f"{orphan_hash}.pdf",)
        assert (private_root / f"{digest}.pdf").is_file()
        assert not (private_root / f"{orphan_hash}.pdf").exists()
        async with factory() as session:
            rows = (await session.scalars(select(MpDrugLeaflet))).all()
            assert len(rows) == 2
            assert {row.stored_filename for row in rows} == {f"{digest}.pdf"}
    finally:
        await engine.dispose()
