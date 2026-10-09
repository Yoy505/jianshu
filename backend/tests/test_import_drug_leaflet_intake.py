"""说明书身份/版本待核队列：只读预检、私有存储、幂等和孤儿清理。"""

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import MpDrugLeafletIntake
from scripts.import_drug_leaflet_intake import (
    IntakeManifestError,
    audit_orphans,
    import_records,
    load_manifest,
)


def _pdf(path: Path, text: str = "private leaflet sample") -> str:
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
    xref = sum(map(len, parts))
    parts.append(b"xref\n0 6\n0000000000 65535 f \n")
    parts.extend(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    parts.append(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    raw = b"".join(parts)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _manifest(tmp_path: Path, source_root: Path, *, claimed: str | None = None) -> Path:
    pdf = source_root / "sample.pdf"
    digest = _pdf(pdf, "CDE intake sample")
    payload = {
        "schema_version": 1,
        "dataset_name": "isolated intake test",
        "records": [{
            "source_namespace": "cde_marketed",
            "source_record_id": "detail-1",
            "source_acceptance_no": "CXZS0000001",
            "source_product_name": "测试说明书",
            "source_url": "https://www.cde.org.cn/main/xxgk/postmarketpage?acceptidCODE=detail-1",
            "source_publication_date": "2026-09-24",
            "claimed_approval_no": claimed,
            "document_date": None,
            "version_note": "日期占位，待人工核验",
            "source_checked_at": "2026-09-24",
            "pdf_path": pdf.name,
            "expected_sha256": digest,
        }],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return manifest


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(MpDrugLeafletIntake.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_manifest_allows_missing_approval_and_version_date_but_rejects_unofficial_url(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(settings, "DRUG_LEAFLET_INTAKE_DIR", str(tmp_path / "private"))
    monkeypatch.setattr(settings, "DRUG_LEAFLET_DIR", str(tmp_path / "other-private"))
    rows = load_manifest(_manifest(tmp_path, source), source)
    assert rows[0].claimed_approval_no is None
    assert rows[0].document_date is None

    payload = json.loads((_manifest(tmp_path, source)).read_text())
    payload["records"][0]["source_url"] = "https://example.org/not-official"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IntakeManifestError, match="官方主机"):
        load_manifest(bad, source)


@pytest.mark.asyncio
async def test_dry_run_apply_repeat_conflict_and_orphan_audit(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    manifest = _manifest(tmp_path, source)
    records = load_manifest(manifest, source)
    intake_root = tmp_path / "private-intake"
    monkeypatch.setattr(settings, "DRUG_LEAFLET_INTAKE_DIR", str(intake_root))
    monkeypatch.setattr(settings, "DRUG_LEAFLET_DIR", str(tmp_path / "existing-private"))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    engine, factory = await _database()
    try:
        preview = await import_records(factory, records)
        assert preview.count("insert") == 1
        assert not intake_root.exists()
        async with factory() as session:
            assert (await session.scalars(select(MpDrugLeafletIntake))).all() == []

        applied = await import_records(factory, records, apply=True)
        assert applied.count("insert") == 1
        async with factory() as session:
            row = await session.scalar(select(MpDrugLeafletIntake))
            assert row.review_status == "pending"
            assert "approval_no_unavailable" in row.review_reasons
            assert "document_date_unavailable" in row.review_reasons
            assert "unmatched_drug" in row.review_reasons
            assert row.distribution_authorization_ref is None
        assert len(list(intake_root.glob("*.pdf"))) == 1
        assert await audit_orphans(factory) == ()

        repeated = await import_records(factory, records, apply=True)
        assert repeated.count("unchanged") == 1

        altered = json.loads(manifest.read_text())
        altered["records"][0]["version_note"] = "changed evidence"
        altered_manifest = tmp_path / "altered.json"
        altered_manifest.write_text(json.dumps(altered, ensure_ascii=False), encoding="utf-8")
        conflict = await import_records(factory, load_manifest(altered_manifest, source), apply=True)
        assert conflict.count("conflict") == 1
        async with factory() as session:
            assert len((await session.scalars(select(MpDrugLeafletIntake))).all()) == 1

        orphan_source = tmp_path / "orphan.pdf"
        orphan_hash = _pdf(orphan_source, "unreferenced orphan sample")
        orphan_bytes = orphan_source.read_bytes()
        orphan = intake_root / f"{orphan_hash}.pdf"
        # The same content is intentionally unreferenced by the intake table.
        orphan.write_bytes(orphan_bytes)
        assert await audit_orphans(factory) == (orphan.name,)
        assert await audit_orphans(factory, remove=True) == (orphan.name,)
        assert not orphan.exists()
    finally:
        await engine.dispose()
