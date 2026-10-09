"""CDE 官方基础事实包：来源校验、只读预览、幂等导入和人工编辑保护。"""

import json
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.mp import MpDrug
from scripts import import_official_drugs as importer
from scripts.import_official_drugs import (
    DEFAULT_MANIFEST,
    ManifestError,
    import_records,
    load_manifest,
)


def test_manifest_contains_only_traced_registration_facts():
    records = load_manifest()

    assert [record.approval_no for record in records] == [
        "国药准字H37020550",
        "国药准字H20066843",
        "国药准字H20066208",
    ]
    assert all(record.source_url.startswith("https://www.cde.org.cn/hymlj/detailPage/") for record in records)
    assert all(record.source_checked_at.isoformat() == "2026-09-24" for record in records)
    assert records[0].license_holder is None
    assert records[1].license_holder is None
    assert records[2].license_holder == "石药集团欧意药业有限公司"


@pytest.mark.parametrize(
    "field,value",
    [
        ("indication", "未经核验的适应症"),
        ("tags", ["otc"]),
        ("brand_name", "未经核验的品牌"),
        ("publication_status", "published"),
        ("source_url", "https://example.com/drug"),
    ],
)
def test_manifest_rejects_unverified_or_nonofficial_fields(tmp_path, field, value):
    payload = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    payload["records"][0][field] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ManifestError):
        load_manifest(path)


def test_manifest_rejects_duplicate_approval_number(tmp_path):
    payload = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    payload["records"][1]["approval_no"] = payload["records"][0]["approval_no"]
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ManifestError, match="批准文号重复"):
        load_manifest(path)


def test_cli_requires_explicit_apply_flag(monkeypatch):
    seen: list[bool] = []

    async def fake_run(records, *, apply):
        assert len(records) == 3
        seen.append(apply)
        return importer.ImportReport(applied=apply, actions=())

    monkeypatch.setattr(importer, "_run_cli", fake_run)
    assert importer.main([]) == 0
    assert importer.main(["--apply"]) == 0
    assert seen == [False, True]


@pytest.mark.asyncio
async def test_dry_run_does_not_write_and_apply_is_idempotent():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(MpDrug.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        records = load_manifest()

        async with factory() as session:
            preview = await import_records(session, records)
            assert not preview.applied
            assert preview.count("insert") == 3
            assert (await session.scalars(select(MpDrug))).all() == []

        async with factory() as session:
            first = await import_records(session, records, apply=True)
            assert first.applied
            assert first.count("insert") == 3
            imported = (await session.scalars(select(MpDrug))).all()
            assert len(imported) == 3
            assert all(drug.publication_status == "pending" for drug in imported)
            assert all(drug.tags == [] for drug in imported)
            assert all(drug.indication == drug.usage_note == drug.contraindication == drug.science == "" for drug in imported)
            assert all(drug.brand_name is None and drug.video_url is None for drug in imported)

        async with factory() as session:
            again = await import_records(session, records, apply=True)
            assert again.count("insert") == 0
            assert again.count("unchanged") == 3
            assert len((await session.scalars(select(MpDrug))).all()) == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_updates_only_pending_facts_and_protects_published_content():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(MpDrug.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        records = load_manifest()

        async with factory() as session:
            await import_records(session, records, apply=True)
            pending = await session.scalar(
                select(MpDrug).where(MpDrug.approval_no == records[0].approval_no)
            )
            published = await session.scalar(
                select(MpDrug).where(MpDrug.approval_no == records[1].approval_no)
            )
            offline = await session.scalar(
                select(MpDrug).where(MpDrug.approval_no == records[2].approval_no)
            )
            pending.science = "编辑中的科普内容"
            published.publication_status = "published"
            published.science = "已审定的科普内容"
            offline.publication_status = "offline"
            offline.science = "已下线的科普内容"
            await session.commit()

        changed_records = (
            replace(records[0], manufacturer="蓬莱诺康药业有限公司（复核）"),
            replace(records[1], manufacturer="不得覆盖的厂商"),
            replace(records[2], manufacturer="不得覆盖的厂商"),
        )
        async with factory() as session:
            preview = await import_records(session, changed_records)
            assert [action.action for action in preview.actions] == [
                "update", "protected", "protected"
            ]
            updated = await import_records(session, changed_records, apply=True)
            assert updated.count("update") == 1
            assert updated.count("protected") == 2

        async with factory() as session:
            pending = await session.scalar(
                select(MpDrug).where(MpDrug.approval_no == records[0].approval_no)
            )
            published = await session.scalar(
                select(MpDrug).where(MpDrug.approval_no == records[1].approval_no)
            )
            offline = await session.scalar(
                select(MpDrug).where(MpDrug.approval_no == records[2].approval_no)
            )
            assert pending.manufacturer == "蓬莱诺康药业有限公司（复核）"
            assert pending.science == "编辑中的科普内容"
            assert published.manufacturer == records[1].manufacturer
            assert published.science == "已审定的科普内容"
            assert offline.manufacturer == records[2].manufacturer
            assert offline.science == "已下线的科普内容"
    finally:
        await engine.dispose()
