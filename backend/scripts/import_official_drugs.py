"""将已核对的 CDE 药品注册基础事实导入待发布区。

默认 dry-run；只有显式传入 --apply 才写库。不会自动建表或发布药品。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import Date, DateTime, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect

# 支持从 backend/ 执行 `python scripts/import_official_drugs.py`。
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.mp import MpDrug  # noqa: E402


DEFAULT_MANIFEST = (
    BACKEND_DIR / "data" / "official_drugs" / "cde_catalog_2026-09-24.json"
)
SOURCE_NAME = "国家药品监督管理局药品审评中心（CDE）化学药品目录集"
RECORD_FIELDS = frozenset(
    {
        "approval_no",
        "common_name",
        "brand_name",
        "form",
        "specification",
        "manufacturer",
        "license_holder",
        "approval_date",
        "initial",
        "tags",
        "indication",
        "usage_note",
        "contraindication",
        "science",
        "video_url",
        "source_name",
        "source_url",
        "source_checked_at",
        "publication_status",
    }
)
FACT_FIELDS = (
    "common_name",
    "form",
    "specification",
    "manufacturer",
    "license_holder",
    "initial",
    "source_name",
    "source_url",
    "source_checked_at",
)
REQUIRED_MODEL_FIELDS = ("approval_no", "publication_status", *FACT_FIELDS)


class ManifestError(ValueError):
    """数据包不符合本次官方基础事实导入契约。"""


@dataclass(frozen=True)
class OfficialDrugRecord:
    approval_no: str
    common_name: str
    form: str
    specification: str
    manufacturer: str
    license_holder: str | None
    approval_date: date
    initial: str
    source_name: str
    source_url: str
    source_checked_at: date


@dataclass(frozen=True)
class ImportAction:
    approval_no: str
    action: str  # insert / update / unchanged / protected


@dataclass(frozen=True)
class ImportReport:
    applied: bool
    actions: tuple[ImportAction, ...]

    def count(self, action: str) -> int:
        return sum(item.action == action for item in self.actions)


def _iso_date(value: object, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ManifestError(f"{field} 必须是 YYYY-MM-DD 日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ManifestError(f"{field} 不是有效日期") from exc


def _required_text(record: dict, field: str, max_length: int) -> str:
    value = record[field]
    if not isinstance(value, str) or not value or value != value.strip():
        raise ManifestError(f"{field} 必须是非空且无首尾空格的字符串")
    if len(value) > max_length:
        raise ManifestError(f"{field} 超过数据库字段长度 {max_length}")
    return value


def _nullable_text(record: dict, field: str, max_length: int) -> str | None:
    value = record[field]
    if value is None:
        return None
    return _required_text(record, field, max_length)


def _official_cde_url(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(
        parsed.scheme == "https"
        and parsed.netloc == "www.cde.org.cn"
        and re.fullmatch(r"/hymlj/detailPage/[0-9a-f]{32}", parsed.path)
        and not parsed.query
        and not parsed.fragment
    )


def load_manifest(path: Path = DEFAULT_MANIFEST) -> tuple[OfficialDrugRecord, ...]:
    """先严格校验数据包，拒绝夹带临床文案或发布状态。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"无法读取 JSON 数据包 {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ManifestError("数据包 schema_version 必须为 1")
    rows = payload.get("records")
    if not isinstance(rows, list) or not rows:
        raise ManifestError("records 必须是非空数组")

    records: list[OfficialDrugRecord] = []
    seen_approval_no: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != RECORD_FIELDS:
            raise ManifestError(f"第 {index} 条字段不符合官方基础事实契约")
        approval_no = _required_text(row, "approval_no", 50)
        if approval_no in seen_approval_no:
            raise ManifestError(f"批准文号重复：{approval_no}")
        seen_approval_no.add(approval_no)
        if (
            row["brand_name"] is not None
            or row["video_url"] is not None
            or row["tags"] != []
            or any(row[field] != "" for field in ("indication", "usage_note", "contraindication", "science"))
            or row["publication_status"] != "pending"
        ):
            raise ManifestError(f"{approval_no} 含有未经本批次核验的内容或非 pending 状态")

        source_name = _required_text(row, "source_name", 200)
        source_url = _required_text(row, "source_url", 500)
        if source_name != SOURCE_NAME or not _official_cde_url(source_url):
            raise ManifestError(f"{approval_no} 的来源必须是 CDE 目录集官方详情页")
        approved = _iso_date(row["approval_date"], "approval_date")
        checked = _iso_date(row["source_checked_at"], "source_checked_at")
        if checked < approved:
            raise ManifestError(f"{approval_no} 的来源核验日期早于批准日期")
        initial = _required_text(row, "initial", 1)
        if not re.fullmatch(r"[A-Z]", initial):
            raise ManifestError(f"{approval_no} 的 initial 必须为 A-Z")
        records.append(
            OfficialDrugRecord(
                approval_no=approval_no,
                common_name=_required_text(row, "common_name", 100),
                form=_required_text(row, "form", 50),
                specification=_required_text(row, "specification", 100),
                manufacturer=_required_text(row, "manufacturer", 100),
                license_holder=_nullable_text(row, "license_holder", 100),
                approval_date=approved,
                initial=initial,
                source_name=source_name,
                source_url=source_url,
                source_checked_at=checked,
            )
        )
    return tuple(records)


def _checked_at_for_model(value: date) -> date | datetime:
    """兼容模型将来源核验日期定义为 Date 或 DateTime。"""
    column_type = inspect(MpDrug).columns["source_checked_at"].type
    if isinstance(column_type, DateTime):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(column_type, Date):
        return value
    raise RuntimeError("MpDrug.source_checked_at 必须是 Date 或 DateTime")


def _model_fact_values(record: OfficialDrugRecord) -> dict:
    return {
        "common_name": record.common_name,
        "form": record.form,
        "specification": record.specification,
        "manufacturer": record.manufacturer,
        "license_holder": record.license_holder,
        "initial": record.initial,
        "source_name": record.source_name,
        "source_url": record.source_url,
        "source_checked_at": _checked_at_for_model(record.source_checked_at),
    }


def _same_fact(field: str, current: object, expected: object) -> bool:
    if field == "source_checked_at" and isinstance(current, datetime):
        return current.date() == (expected.date() if isinstance(expected, datetime) else expected)
    return current == expected


async def import_records(
    session: AsyncSession,
    records: tuple[OfficialDrugRecord, ...],
    *,
    apply: bool = False,
) -> ImportReport:
    """按批准文号规划或执行写入；已审核发布的内容永不改写。"""
    model_fields = set(inspect(MpDrug).columns.keys())
    missing = set(REQUIRED_MODEL_FIELDS) - model_fields
    if missing:
        raise RuntimeError(f"MpDrug 缺少导入所需字段：{', '.join(sorted(missing))}；请先迁移")

    actions: list[ImportAction] = []
    try:
        for record in records:
            existing = await session.scalar(
                select(MpDrug).where(MpDrug.approval_no == record.approval_no)
            )
            facts = _model_fact_values(record)
            if existing is None:
                actions.append(ImportAction(record.approval_no, "insert"))
                if apply:
                    session.add(
                        MpDrug(
                            approval_no=record.approval_no,
                            publication_status="pending",
                            brand_name=None,
                            tags=[],
                            indication="",
                            usage_note="",
                            contraindication="",
                            science="",
                            video_url=None,
                            **facts,
                        )
                    )
                continue

            # 任何非 pending 状态都按已进入人工流程处理，安全起见只读。
            if existing.publication_status != "pending":
                actions.append(ImportAction(record.approval_no, "protected"))
                continue
            changes = {
                field: value
                for field, value in facts.items()
                if not _same_fact(field, getattr(existing, field), value)
            }
            if not changes:
                actions.append(ImportAction(record.approval_no, "unchanged"))
                continue
            actions.append(ImportAction(record.approval_no, "update"))
            if apply:
                for field, value in changes.items():
                    setattr(existing, field, value)
        if apply:
            await session.commit()
    except Exception:
        if apply:
            await session.rollback()
        raise
    return ImportReport(applied=apply, actions=tuple(actions))


async def _run_cli(records: tuple[OfficialDrugRecord, ...], *, apply: bool) -> ImportReport:
    # 仅在已完成本地数据包校验后创建数据库会话。
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        return await import_records(session, records, apply=apply)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="CDE JSON 数据包")
    parser.add_argument("--apply", action="store_true", help="显式允许写入数据库；默认只预览")
    args = parser.parse_args(argv)
    try:
        records = load_manifest(args.manifest)
        report = asyncio.run(_run_cli(records, apply=args.apply))
    except Exception as exc:
        print(f"[official-drugs] 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    mode = "已写入" if report.applied else "仅预览，未写入"
    print(f"[official-drugs] {mode}；共 {len(report.actions)} 条")
    for item in report.actions:
        print(f"  {item.approval_no}: {item.action}")
    print(
        "[official-drugs] 新增 {insert}，更新 {update}，无变化 {unchanged}，"
        "受保护跳过 {protected}。所有新增记录均为 pending。".format(
            insert=report.count("insert"),
            update=report.count("update"),
            unchanged=report.count("unchanged"),
            protected=report.count("protected"),
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
