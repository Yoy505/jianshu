"""全类别监管来源只暂存：身份键、状态、分层对账和幂等。"""

import hashlib
import json
import stat
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import MpDrug, MpDrugRegulatoryObservation, MpDrugRegulatorySource
from scripts import import_drug_regulatory_sources as importer
from scripts.import_drug_regulatory_sources import (
    RegulatoryManifestError,
    audit_orphans,
    import_records,
    load_manifest,
)


def _manifest(tmp_path):
    source_root = tmp_path / "private-source"
    source_root.mkdir()
    original = source_root / "regulator-export.json"
    original.write_bytes(b'{"source":"offline-test-only"}\n')
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    cases = [
        ("nmpa.chemical_domestic", "chem-d", "TEST-H-1", "chemical", "domestic", "approved_current", "有效", "2026-09-24"),
        ("nmpa.chemical_imported", "chem-i", "TEST-X-1", "chemical", "imported", "unknown", "已批准", None),
        ("nmpa.tcm_domestic", "tcm-d", "TEST-Z-1", "tcm", "domestic", "unknown", None, None),
        ("nmpa.biologic_domestic", "bio-d", "TEST-S-1", "biologic", "domestic", "revoked", "已撤销", "2026-09-20"),
        ("nmpa.biologic_imported", "bio-i", "TEST-X-1", "biologic", "imported", "unknown", None, None),
    ]
    rows = []
    for namespace, source_id, identifier, drug_class, origin, status, raw_status, as_of in cases:
        rows.append({
            "source_namespace": namespace,
            "source_record_id": source_id,
            "registration_identifier": identifier,
            "source_product_name": f"TEST PRODUCT {source_id}",
            "drug_class": drug_class,
            "origin": origin,
            "regulatory_status": status,
            "source_status_text": raw_status,
            "regulatory_as_of": as_of,
            "source_url": f"https://www.nmpa.gov.cn/test/{source_id}",
            "source_document_path": original.name,
            "source_document_sha256": digest,
            "source_checked_at": "2026-09-24",
        })
    payload = {"schema_version": 1, "dataset_name": "isolated test fixtures", "records": rows}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return manifest, source_root, payload


def _rewrite(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_manifest_accepts_five_approved_drug_source_categories_without_inference(tmp_path):
    manifest, source_root, _ = _manifest(tmp_path)
    rows = load_manifest(manifest, source_root)
    assert len(rows) == 5
    assert {(row.drug_class, row.origin) for row in rows} == {
        ("chemical", "domestic"), ("chemical", "imported"),
        ("tcm", "domestic"), ("biologic", "domestic"), ("biologic", "imported"),
    }
    assert rows[1].regulatory_status == "unknown"  # “已批准”未被推断为现行有效
    assert rows[2].registration_identifier == "TEST-Z-1"


def test_manifest_rejects_unknown_status_or_ambiguous_current_claim(tmp_path):
    manifest, source_root, payload = _manifest(tmp_path)
    payload["records"][0]["regulatory_status"] = "marketed"
    _rewrite(manifest, payload)
    with pytest.raises(RegulatoryManifestError, match="监管状态未知"):
        load_manifest(manifest, source_root)

    payload["records"][0]["regulatory_status"] = "approved_current"
    payload["records"][0]["source_status_text"] = "已批准"
    _rewrite(manifest, payload)
    with pytest.raises(RegulatoryManifestError, match="缺少明确现行有效"):
        load_manifest(manifest, source_root)


def test_manifest_rejects_duplicate_source_key_and_hash_mismatch(tmp_path):
    manifest, source_root, payload = _manifest(tmp_path)
    payload["records"][1]["source_namespace"] = payload["records"][0]["source_namespace"]
    payload["records"][1]["source_record_id"] = payload["records"][0]["source_record_id"]
    _rewrite(manifest, payload)
    with pytest.raises(RegulatoryManifestError, match="来源键重复"):
        load_manifest(manifest, source_root)

    payload["records"][1]["source_record_id"] = "unique-again"
    payload["records"][1]["source_document_sha256"] = "0" * 64
    _rewrite(manifest, payload)
    with pytest.raises(RegulatoryManifestError, match="SHA-256 不匹配"):
        load_manifest(manifest, source_root)


def test_exact_state_platform_host_is_allowed_but_lookalikes_are_not(tmp_path):
    manifest, source_root, payload = _manifest(tmp_path)
    payload["records"][0]["source_url"] = (
        "https://app.gjzwfw.gov.cn/jmopen/webapp/html5/datasearchgcyp/index.html"
    )
    _rewrite(manifest, payload)
    assert load_manifest(manifest, source_root)[0].source_url.startswith(
        "https://app.gjzwfw.gov.cn/"
    )

    for host in ("app.gjzwfw.gov.cn.evil.example", "www.nmpaic.org.cn"):
        payload["records"][0]["source_url"] = f"https://{host}/drug"
        _rewrite(manifest, payload)
        with pytest.raises(RegulatoryManifestError, match="主机白名单"):
            load_manifest(manifest, source_root)


def test_report_cannot_be_written_under_public_upload_dir(tmp_path, monkeypatch):
    manifest, source_root, _ = _manifest(tmp_path)
    public = tmp_path / "uploads"
    public.mkdir()
    monkeypatch.setattr(importer.settings, "UPLOAD_DIR", str(public))
    assert importer.main([
        "--manifest", str(manifest), "--source-root", str(source_root),
        "--apply", "--report", str(public / "report.json"),
    ]) == 1
    assert not (public / "report.json").exists()


def test_apply_requires_durable_report_path(tmp_path):
    manifest, source_root, _ = _manifest(tmp_path)
    with pytest.raises(SystemExit) as exc:
        importer.main([
            "--manifest", str(manifest), "--source-root", str(source_root), "--apply",
        ])
    assert exc.value.code == 2


def test_source_file_cannot_hide_under_public_upload_dir(tmp_path, monkeypatch):
    manifest, _, payload = _manifest(tmp_path)
    public = tmp_path / "uploads"
    public.mkdir()
    original = public / "source.json"
    original.write_bytes(b"public source")
    payload["records"][0]["source_document_path"] = "uploads/source.json"
    payload["records"][0]["source_document_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
    _rewrite(manifest, payload)
    monkeypatch.setattr(importer.settings, "UPLOAD_DIR", str(public))
    with pytest.raises(RegulatoryManifestError, match="来源原件不能位于公开"):
        load_manifest(manifest, tmp_path)


def test_relative_storage_paths_ignore_cli_current_directory(tmp_path, monkeypatch):
    from app.core import storage_paths

    backend_root = tmp_path / "fake-backend"
    backend_root.mkdir()
    monkeypatch.setattr(storage_paths, "BACKEND_DIR", backend_root)
    monkeypatch.setattr(importer.settings, "UPLOAD_DIR", "./uploads")
    monkeypatch.setattr(
        importer.settings, "DRUG_REGULATORY_SOURCE_DIR", "./private-regulatory"
    )
    monkeypatch.chdir(tmp_path)
    assert importer._public_upload_dir() == backend_root / "uploads"
    assert importer.private_source_dir(create=False) == backend_root / "private-regulatory"


def test_conflict_report_is_private_and_returns_partial_success_code(tmp_path, monkeypatch):
    manifest, source_root, _ = _manifest(tmp_path)
    report_path = tmp_path / "private-report.json"

    async def fake_run(records, *, apply, batch_size, progress):
        assert apply and batch_size == 100 and len(records) == 5
        action = importer.ImportAction(
                "nmpa.chemical_domestic", "chem-d", "TEST-H-1",
                "chemical", "domestic", "approved_current",
                "a" * 64, "source.json", "conflict", None, 0, (),
            )
        progress(1, 1, (action,))
        return importer.ImportReport(applied=True, actions=(action,))

    monkeypatch.setattr(importer, "_run_cli", fake_run)
    assert importer.main([
        "--manifest", str(manifest), "--source-root", str(source_root),
        "--apply", "--report", str(report_path),
    ]) == 2
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    events = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["started", "batch_committed", "complete"]
    assert events[-1]["summary"]["conflict"] == 1


@pytest.mark.asyncio
async def test_dry_run_apply_idempotency_duplicates_and_conflict(tmp_path, monkeypatch):
    manifest, source_root, _ = _manifest(tmp_path)
    records = load_manifest(manifest, source_root)
    private_root = tmp_path / "private-regulatory-sources"
    monkeypatch.setattr(importer.settings, "DRUG_REGULATORY_SOURCE_DIR", str(private_root))
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(MpDrug.__table__.create)
            await connection.run_sync(MpDrugRegulatorySource.__table__.create)
            await connection.run_sync(MpDrugRegulatoryObservation.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(MpDrug(
                common_name="TEST CANONICAL", approval_no="TEST-H-1",
                publication_status="pending", form="tablet", manufacturer="TEST", tags=[],
                indication="", usage_note="", contraindication="", science="", initial="T",
            ))
            await session.commit()

        preview = await import_records(factory, records, batch_size=2)
        assert not preview.applied
        assert preview.count("insert") == 5
        assert preview.unmatched_count == 4
        assert preview.duplicate_identifier_count == 2
        assert preview.strata == {
            "biologic/domestic/revoked": 1,
            "biologic/imported/unknown": 1,
            "chemical/domestic/approved_current": 1,
            "chemical/imported/unknown": 1,
            "tcm/domestic/unknown": 1,
        }
        assert preview.strata_by_action == {"insert": preview.strata}
        assert preview.by_source_namespace == {
            "nmpa.biologic_domestic": 1,
            "nmpa.biologic_imported": 1,
            "nmpa.chemical_domestic": 1,
            "nmpa.chemical_imported": 1,
            "nmpa.tcm_domestic": 1,
        }
        async with factory() as session:
            assert (await session.scalars(select(MpDrugRegulatorySource))).all() == []
        assert not private_root.exists()

        first = await import_records(factory, records, apply=True, batch_size=2)
        assert first.count("insert") == 5
        assert first.strata == preview.strata
        async with factory() as session:
            staged = (await session.scalars(select(MpDrugRegulatorySource))).all()
            assert len(staged) == 5
            assert all(row.review_status == "pending" and row.canonical_drug_id is None for row in staged)
            assert all(row.stored_document_key == f"{row.source_document_sha256}.blob" for row in staged)
            assert sum(row.registration_identifier == "TEST-X-1" for row in staged) == 2
            observations = (await session.scalars(select(MpDrugRegulatoryObservation))).all()
            assert len(observations) == 5
            assert all(row.review_status == "pending" for row in observations)
        assert len(list(private_root.glob("*.blob"))) == 1  # 五条来源共用一个原件
        assert await audit_orphans(factory) == ()

        repeat = await import_records(factory, records, apply=True, batch_size=3)
        assert repeat.count("unchanged") == 5
        assert repeat.duplicate_identifier_count == 2

        changed = replace(records[0], regulatory_status="unknown", source_status_text="已批准")
        new_record = replace(
            records[2], source_record_id="tcm-d-next", registration_identifier="TEST-Z-2"
        )
        conflict = await import_records(factory, (changed, new_record), apply=True, batch_size=1)
        assert conflict.count("conflict") == 1
        assert conflict.count("insert") == 1  # 冲突不回滚其他已提交批次
        async with factory() as session:
            row = await session.scalar(select(MpDrugRegulatorySource).where(
                MpDrugRegulatorySource.source_record_id == "chem-d"
            ))
            assert row.regulatory_status == "approved_current"
            assert row.source_status_text == "有效"
            same_file_conflicts = (await session.scalars(select(
                MpDrugRegulatoryObservation
            ).where(
                MpDrugRegulatoryObservation.source_id == row.id,
                MpDrugRegulatoryObservation.kind == "conflict",
            ))).all()
            assert len(same_file_conflicts) == 1
            assert same_file_conflicts[0].reported_facts["regulatory_status"] == "unknown"
            assert same_file_conflicts[0].review_status == "pending"
            assert len((await session.scalars(select(MpDrugRegulatorySource))).all()) == 6

        later = replace(records[0], source_checked_at=date(2026, 9, 25))
        recheck = await import_records(factory, (later,), apply=True)
        assert recheck.count("rechecked") == 1
        async with factory() as session:
            row = await session.scalar(select(MpDrugRegulatorySource).where(
                MpDrugRegulatorySource.source_record_id == "chem-d"
            ))
            assert row.source_checked_at == date(2026, 9, 25)
            assert row.regulatory_status == "approved_current"
            canonical = await session.scalar(select(MpDrug).where(MpDrug.approval_no == "TEST-H-1"))
            old_observation = await session.scalar(select(MpDrugRegulatoryObservation).where(
                MpDrugRegulatoryObservation.source_id == row.id,
                MpDrugRegulatoryObservation.kind == "matching",
            ))
            row.review_status = "accepted"
            row.canonical_drug_id = canonical.id
            old_observation.review_status = "accepted"
            await session.commit()

        new_bytes = b'{"source":"offline-test-only","revision":2}\n'
        records[0].source_document_path.write_bytes(new_bytes)
        new_hash = hashlib.sha256(new_bytes).hexdigest()
        next_snapshot = replace(
            records[0], source_document_sha256=new_hash,
            source_checked_at=date(2026, 9, 26), manifest_sha256="f" * 64,
        )
        refreshed = await import_records(factory, (next_snapshot,), apply=True)
        assert refreshed.count("new_snapshot") == 1
        async with factory() as session:
            row = await session.scalar(select(MpDrugRegulatorySource).where(
                MpDrugRegulatorySource.source_record_id == "chem-d"
            ))
            assert row.source_document_sha256 == new_hash
            assert row.review_status == "pending"
            assert row.canonical_drug_id == canonical.id
            observations = (await session.scalars(select(MpDrugRegulatoryObservation).where(
                MpDrugRegulatoryObservation.source_id == row.id
            ))).all()
            assert {observation.source_document_sha256 for observation in observations} == {
                records[0].source_document_sha256, new_hash,
            }
            assert sum(
                obs.kind == "matching" and obs.review_status == "accepted"
                and obs.source_document_sha256 == records[0].source_document_sha256
                for obs in observations
            ) == 1
            assert sum(
                obs.kind == "conflict" and obs.review_status == "pending"
                for obs in observations
            ) == 1
            assert sum(
                obs.kind == "matching" and obs.review_status == "pending"
                and obs.source_document_sha256 == new_hash
                for obs in observations
            ) == 1
        assert len(list(private_root.glob("*.blob"))) == 2
        assert await audit_orphans(factory) == ()

        relocated = replace(
            next_snapshot,
            source_url="https://www.nmpa.gov.cn/test/relocated-chem-d",
            source_checked_at=date(2026, 9, 27),
        )
        moved = await import_records(factory, (relocated,), apply=True)
        assert moved.count("new_snapshot") == 1
        async with factory() as session:
            row = await session.scalar(select(MpDrugRegulatorySource).where(
                MpDrugRegulatorySource.source_record_id == "chem-d"
            ))
            assert row.source_url == relocated.source_url
            observations = (await session.scalars(select(MpDrugRegulatoryObservation).where(
                MpDrugRegulatoryObservation.source_id == row.id
            ))).all()
            # same content under a relocated source URL is a distinct evidence snapshot;
            # the earlier content, URL snapshot, and conflict are retained.
            assert len(observations) == 4
            assert sum(obs.source_document_sha256 == new_hash for obs in observations) == 2
            row.review_status = "accepted"
            latest_observation = next(
                obs for obs in observations if obs.source_url == relocated.source_url
            )
            latest_observation.review_status = "accepted"
            await session.commit()
        assert len(list(private_root.glob("*.blob"))) == 2

        revoked_bytes = b'{"source":"offline-test-only","status":"revoked"}\n'
        records[0].source_document_path.write_bytes(revoked_bytes)
        revoked_hash = hashlib.sha256(revoked_bytes).hexdigest()
        revoked = replace(
            relocated, regulatory_status="revoked", source_status_text="已撤销",
            regulatory_as_of=date(2026, 9, 28),
            source_document_sha256=revoked_hash,
            source_checked_at=date(2026, 9, 28),
        )
        adverse = await import_records(factory, (revoked,), apply=True)
        assert adverse.count("conflict") == 1
        async with factory() as session:
            row = await session.scalar(select(MpDrugRegulatorySource).where(
                MpDrugRegulatorySource.source_record_id == "chem-d"
            ))
            assert row.regulatory_status == "approved_current"  # 旧事实留待人工裁决
            assert row.review_status == "pending"  # 不再沿用旧 accepted 门禁
            assert row.source_document_sha256 == new_hash  # 未把冲突来件当当前真源
            conflict_observation = await session.scalar(select(
                MpDrugRegulatoryObservation
            ).where(
                MpDrugRegulatoryObservation.source_id == row.id,
                MpDrugRegulatoryObservation.source_document_sha256 == revoked_hash,
            ))
            assert conflict_observation.kind == "conflict"
            assert conflict_observation.review_status == "pending"
            assert conflict_observation.reported_facts["regulatory_status"] == "revoked"
        assert len(list(private_root.glob("*.blob"))) == 3
        assert await audit_orphans(factory) == ()

        orphan_bytes = b"unreferenced regulatory source"
        orphan_hash = hashlib.sha256(orphan_bytes).hexdigest()
        orphan = private_root / f"{orphan_hash}.blob"
        orphan.write_bytes(orphan_bytes)
        assert await audit_orphans(factory) == (orphan.name,)
        assert await audit_orphans(factory, remove=True) == (orphan.name,)
        assert not orphan.exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_identifier_report_is_bounded_at_large_group(tmp_path, monkeypatch):
    manifest, source_root, _ = _manifest(tmp_path)
    base = load_manifest(manifest, source_root)[0]
    records = tuple(replace(
        base, source_record_id=f"same-number-{index:04d}",
        registration_identifier="TEST-SHARED",
    ) for index in range(1000))
    monkeypatch.setattr(
        importer.settings, "DRUG_REGULATORY_SOURCE_DIR", str(tmp_path / "unused-private")
    )
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(MpDrug.__table__.create)
            await connection.run_sync(MpDrugRegulatorySource.__table__.create)
            await connection.run_sync(MpDrugRegulatoryObservation.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        report = await import_records(factory, records, batch_size=100)
        assert report.count("insert") == 1000
        assert report.duplicate_identifier_count == 1000
        assert all(row.duplicate_identifier_count == 999 for row in report.actions)
        assert all(len(row.duplicate_identifier_examples) <= 5 for row in report.actions)
        assert not (tmp_path / "unused-private").exists()
    finally:
        await engine.dispose()
