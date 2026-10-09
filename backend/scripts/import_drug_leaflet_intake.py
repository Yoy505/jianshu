"""离线暂存待核药品说明书 PDF；不关联药品、不发布、不联网。

默认 dry-run。--apply 仅向独立私有目录和 mp_drug_leaflet_intake
写入待核原件。受理号、资料发布日期和 PDF 内声称的批准文号均不能
自动证明当前批准状态、说明书版本或公开转载许可。
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from pypdf import PdfReader
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.core.storage_paths import configured_storage_path  # noqa: E402
from app.models import MpDrug, MpDrugLeafletIntake  # noqa: E402


MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_PDF_PAGES = 300
CHUNK_BYTES = 1024 * 1024
OFFICIAL_HOSTS = frozenset({
    "www.cde.org.cn", "www.nmpa.gov.cn", "app1.nmpa.gov.cn", "app.gjzwfw.gov.cn",
})
MANIFEST_FIELDS = frozenset({"schema_version", "dataset_name", "records"})
RECORD_FIELDS = frozenset({
    "source_namespace", "source_record_id", "source_acceptance_no",
    "source_product_name", "source_url", "source_publication_date",
    "claimed_approval_no", "document_date", "version_note",
    "source_checked_at", "pdf_path", "expected_sha256",
})


class IntakeManifestError(ValueError):
    """离线清单或 PDF 原件不符合暂存契约。"""


@dataclass(frozen=True)
class PreparedIntake:
    source_namespace: str
    source_record_id: str
    source_acceptance_no: str | None
    source_product_name: str
    source_url: str
    source_publication_date: date | None
    claimed_approval_no: str | None
    document_date: date | None
    version_note: str | None
    source_checked_at: date
    pdf_path: Path
    original_filename: str
    file_size: int
    file_sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class IntakeAction:
    source_namespace: str
    source_record_id: str
    file_sha256: str
    action: str
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class IntakeReport:
    applied: bool
    actions: tuple[IntakeAction, ...]

    def count(self, action: str) -> int:
        return sum(row.action == action for row in self.actions)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise IntakeManifestError(f"JSON 字段重复：{key}")
        result[key] = value
    return result


def _required_text(record: dict, field: str, maximum: int) -> str:
    value = record[field]
    if not isinstance(value, str) or not value or value != value.strip():
        raise IntakeManifestError(f"{field} 必须是非空且无首尾空格的字符串")
    if len(value) > maximum or any(ord(c) < 32 for c in value):
        raise IntakeManifestError(f"{field} 超长或含控制字符")
    return value


def _optional_text(record: dict, field: str, maximum: int) -> str | None:
    return None if record[field] is None else _required_text(record, field, maximum)


def _iso_date(value: object, field: str, *, optional: bool = False) -> date | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise IntakeManifestError(f"{field} 必须是 YYYY-MM-DD 日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise IntakeManifestError(f"{field} 不是有效日期") from exc


def _official_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS
        or parsed.username or parsed.password or parsed.fragment
        or any(ord(c) < 32 for c in value)
    ):
        raise IntakeManifestError("source_url 须为已核实官方主机的无凭据 HTTPS 链接")


def _pdf_source_path(source_root: Path, relative_name: str) -> Path:
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".pdf":
        raise IntakeManifestError("pdf_path 必须是 source-root 内的相对 PDF 路径")
    candidate = source_root / relative
    if candidate.is_symlink():
        raise IntakeManifestError(f"不接受符号链接原件：{relative_name}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(source_root) or not resolved.is_file():
        raise IntakeManifestError(f"找不到 source-root 内的 PDF：{relative_name}")
    if len(candidate.name) > 255:
        raise IntakeManifestError("PDF 文件名过长")
    return resolved


def _hash_valid_pdf(path: Path) -> tuple[str, int]:
    size = path.stat().st_size
    if not 32 <= size <= MAX_PDF_BYTES:
        raise IntakeManifestError(f"PDF 大小须在 32 字节至 30 MiB：{path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise IntakeManifestError(f"不是 PDF 原件：{path.name}")
        source.seek(0)
        read_size = 0
        while chunk := source.read(CHUNK_BYTES):
            read_size += len(chunk)
            digest.update(chunk)
        if read_size != size:
            raise IntakeManifestError(f"PDF 在核验时发生变化：{path.name}")
        source.seek(max(0, size - 2048))
        if b"%%EOF" not in source.read():
            raise IntakeManifestError(f"PDF 缺少结束标记：{path.name}")
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise IntakeManifestError(f"PDF 加密或页数超限：{path.name}")
    except IntakeManifestError:
        raise
    except Exception as exc:
        raise IntakeManifestError(f"PDF 格式无法可靠解析：{path.name}") from exc
    return digest.hexdigest(), size


def private_intake_dir(*, create: bool) -> Path:
    root = configured_storage_path(settings.DRUG_LEAFLET_INTAKE_DIR)
    public = configured_storage_path(settings.UPLOAD_DIR)
    leaflets = configured_storage_path(settings.DRUG_LEAFLET_DIR)
    if root == public or root.is_relative_to(public):
        raise RuntimeError("待核目录不能位于公开 UPLOAD_DIR 内")
    if root == leaflets or root.is_relative_to(leaflets) or leaflets.is_relative_to(root):
        raise RuntimeError("待核目录必须与既有说明书目录隔离")
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
    return root


def load_manifest(manifest_path: Path, source_root: Path) -> tuple[PreparedIntake, ...]:
    """全清单预检：字段、路径、原件格式和 SHA 均在首次写库前验证。"""
    source_root = source_root.expanduser().resolve()
    if not source_root.is_dir():
        raise IntakeManifestError(f"source-root 不是目录：{source_root}")
    public = configured_storage_path(settings.UPLOAD_DIR)
    target = private_intake_dir(create=False)
    if source_root == public or source_root.is_relative_to(public):
        raise IntakeManifestError("source-root 不能位于公开 UPLOAD_DIR 内")
    if source_root == target or source_root.is_relative_to(target):
        raise IntakeManifestError("source-root 不能是待核私有目录")
    try:
        raw = manifest_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntakeManifestError(f"无法读取清单：{exc}") from exc
    if not isinstance(payload, dict) or set(payload) != MANIFEST_FIELDS:
        raise IntakeManifestError("清单顶层字段不符合契约")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise IntakeManifestError("schema_version 必须为 1")
    _required_text(payload, "dataset_name", 200)
    rows = payload["records"]
    if not isinstance(rows, list) or not rows:
        raise IntakeManifestError("records 必须是非空数组")
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    records: list[PreparedIntake] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != RECORD_FIELDS:
            raise IntakeManifestError(f"第 {index} 条字段不符合待核清单契约")
        source_namespace = _required_text(row, "source_namespace", 64)
        if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]*", source_namespace):
            raise IntakeManifestError("source_namespace 只能是小写来源标识")
        source_record_id = _required_text(row, "source_record_id", 200)
        source_url = _required_text(row, "source_url", 1000)
        _official_url(source_url)
        relative_name = _required_text(row, "pdf_path", 500)
        pdf_path = _pdf_source_path(source_root, relative_name)
        file_sha256, file_size = _hash_valid_pdf(pdf_path)
        expected_sha256 = _required_text(row, "expected_sha256", 64)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise IntakeManifestError("expected_sha256 必须是 64 位小写十六进制")
        if file_sha256 != expected_sha256:
            raise IntakeManifestError(f"第 {index} 条 PDF SHA-256 与清单不一致")
        key = (source_namespace, source_record_id, file_sha256)
        if key in seen:
            raise IntakeManifestError(f"同来源、同原件在清单中重复：{source_record_id}")
        seen.add(key)
        records.append(PreparedIntake(
            source_namespace=source_namespace,
            source_record_id=source_record_id,
            source_acceptance_no=_optional_text(row, "source_acceptance_no", 100),
            source_product_name=_required_text(row, "source_product_name", 200),
            source_url=source_url,
            source_publication_date=_iso_date(
                row["source_publication_date"], "source_publication_date", optional=True
            ),
            claimed_approval_no=_optional_text(row, "claimed_approval_no", 100),
            document_date=_iso_date(row["document_date"], "document_date", optional=True),
            version_note=_optional_text(row, "version_note", 500),
            source_checked_at=_iso_date(row["source_checked_at"], "source_checked_at"),
            pdf_path=pdf_path,
            original_filename=pdf_path.name,
            file_size=file_size,
            file_sha256=file_sha256,
            manifest_sha256=manifest_sha256,
        ))
    return tuple(records)


@contextmanager
def _storage_lock(root: Path):
    descriptor = os.open(root / ".import.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _store_pdf(record: PreparedIntake, root: Path) -> tuple[str, bool]:
    """原子复制到独立私有目录；重用相同 SHA 文件前再次核验。"""
    name = f"{record.file_sha256}.pdf"
    target = root / name
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise RuntimeError(f"私有存储目标异常：{name}")
        saved_hash, saved_size = _hash_valid_pdf(target)
        if (saved_hash, saved_size) != (record.file_sha256, record.file_size):
            raise RuntimeError(f"私有文件哈希冲突或损坏：{name}")
        return name, False

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".incoming-", suffix=".pdf", dir=root, delete=False
        ) as output:
            temporary_path = Path(output.name)
            os.chmod(temporary_path, 0o600)
            digest = hashlib.sha256()
            copied = 0
            with record.pdf_path.open("rb") as source:
                while chunk := source.read(CHUNK_BYTES):
                    copied += len(chunk)
                    if copied > MAX_PDF_BYTES:
                        raise IntakeManifestError("原件在预检后变更且超过大小限制")
                    digest.update(chunk)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if (digest.hexdigest(), copied) != (record.file_sha256, record.file_size):
            raise IntakeManifestError("原件在预检后发生变化，已停止暂存")
        try:
            os.link(temporary_path, target)
            created = True
        except FileExistsError:
            created = False
            saved_hash, saved_size = _hash_valid_pdf(target)
            if (saved_hash, saved_size) != (record.file_sha256, record.file_size):
                raise RuntimeError(f"私有文件哈希冲突或损坏：{name}")
        return name, created
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _review_reasons(
    record: PreparedIntake, drug_status_by_approval: dict[str, str]
) -> tuple[str, ...]:
    reasons = ["manual_identity_review"]
    if record.claimed_approval_no is None:
        reasons.append("approval_no_unavailable")
    if record.document_date is None:
        reasons.append("document_date_unavailable")
    status = drug_status_by_approval.get(record.claimed_approval_no or "")
    if status is None:
        reasons.append("unmatched_drug")
    elif status == "demo":
        reasons.append("demo_drug_blocked")
    return tuple(reasons)


def _facts_equal(row: MpDrugLeafletIntake, record: PreparedIntake) -> bool:
    """清单再次出现时不覆盖原有来源事实、人工状态或原件。"""
    return (
        row.source_acceptance_no == record.source_acceptance_no
        and row.source_product_name == record.source_product_name
        and row.source_url == record.source_url
        and row.source_publication_date == record.source_publication_date
        and row.claimed_approval_no == record.claimed_approval_no
        and row.document_date == record.document_date
        and row.version_note == record.version_note
        and row.original_filename == record.original_filename
        and row.file_size == record.file_size
        and row.stored_filename == f"{record.file_sha256}.pdf"
    )


async def _clean_failed_batch(
    session_factory: async_sessionmaker[AsyncSession],
    root: Path,
    created_names: set[str],
) -> None:
    if not created_names:
        return
    try:
        async with session_factory() as session:
            referenced = set((await session.scalars(
                select(MpDrugLeafletIntake.stored_filename).where(
                    MpDrugLeafletIntake.stored_filename.in_(created_names)
                )
            )).all())
    except Exception as exc:
        raise RuntimeError(
            "批次提交失败且数据库不可查；恢复数据库后运行 --audit-orphans"
        ) from exc
    for name in created_names - referenced:
        path = root / name
        if path.is_symlink():
            raise RuntimeError(f"待回收文件是符号链接：{name}")
        path.unlink(missing_ok=True)


async def audit_orphans(
    session_factory: async_sessionmaker[AsyncSession], *, remove: bool = False
) -> tuple[str, ...]:
    """仅对账独立待核目录，不触碰既有说明书原件。"""
    root = private_intake_dir(create=False)
    if not root.exists():
        return ()
    with _storage_lock(root):
        names = {
            path.name for path in root.iterdir()
            if re.fullmatch(r"[0-9a-f]{64}\.pdf", path.name) and path.is_file()
        }
        async with session_factory() as session:
            referenced = set((await session.scalars(
                select(MpDrugLeafletIntake.stored_filename)
            )).all())
        orphans = tuple(sorted(names - referenced))
        if remove:
            for name in orphans:
                path = root / name
                if path.is_symlink():
                    raise RuntimeError(f"待清理文件是符号链接：{name}")
                stored_hash, _ = _hash_valid_pdf(path)
                if stored_hash != name[:-4]:
                    raise RuntimeError(f"文件内容与哈希名不一致：{name}")
                path.unlink()
        return orphans


async def _import_records_impl(
    session_factory: async_sessionmaker[AsyncSession],
    records: tuple[PreparedIntake, ...],
    root: Path,
    *,
    apply: bool,
    batch_size: int,
    progress: Callable[[int, int], None] | None,
) -> IntakeReport:
    if not 1 <= batch_size <= 300:
        raise ValueError("batch_size 必须在 1 到 300 之间")
    actions: list[IntakeAction] = []
    total_batches = (len(records) + batch_size - 1) // batch_size
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        created_names: set[str] = set()
        try:
            async with session_factory() as session:
                source_keys = {
                    (row.source_namespace, row.source_record_id, row.file_sha256)
                    for row in batch
                }
                existing = (await session.scalars(
                    select(MpDrugLeafletIntake).where(tuple_(
                        MpDrugLeafletIntake.source_namespace,
                        MpDrugLeafletIntake.source_record_id,
                        MpDrugLeafletIntake.file_sha256,
                    ).in_(source_keys))
                )).all()
                existing_by_key = {
                    (row.source_namespace, row.source_record_id, row.file_sha256): row
                    for row in existing
                }
                approvals = {
                    row.claimed_approval_no for row in batch if row.claimed_approval_no
                }
                drug_rows = (
                    (await session.execute(
                        select(MpDrug.approval_no, MpDrug.publication_status).where(
                            MpDrug.approval_no.in_(approvals)
                        )
                    )).all()
                    if approvals else []
                )
                drug_status_by_approval = {
                    approval_no: status for approval_no, status in drug_rows
                }
                for record in batch:
                    key = (
                        record.source_namespace, record.source_record_id,
                        record.file_sha256,
                    )
                    previous = existing_by_key.get(key)
                    reasons = _review_reasons(record, drug_status_by_approval)
                    if previous is not None:
                        if _facts_equal(previous, record):
                            action = "unchanged"
                            reasons = tuple(previous.review_reasons)
                            if apply:
                                _store_pdf(record, root)
                        else:
                            action = "conflict"
                            reasons = tuple(previous.review_reasons)
                    else:
                        action = "insert"
                        if apply:
                            stored_filename, created = _store_pdf(record, root)
                            if created:
                                created_names.add(stored_filename)
                            session.add(MpDrugLeafletIntake(
                                source_namespace=record.source_namespace,
                                source_record_id=record.source_record_id,
                                source_acceptance_no=record.source_acceptance_no,
                                source_product_name=record.source_product_name,
                                source_url=record.source_url,
                                source_publication_date=record.source_publication_date,
                                claimed_approval_no=record.claimed_approval_no,
                                document_date=record.document_date,
                                version_note=record.version_note,
                                source_checked_at=record.source_checked_at,
                                original_filename=record.original_filename,
                                file_size=record.file_size,
                                file_sha256=record.file_sha256,
                                stored_filename=stored_filename,
                                manifest_sha256=record.manifest_sha256,
                                review_reasons=list(reasons),
                                distribution_authorization_ref=None,
                                review_status="pending",
                            ))
                    actions.append(IntakeAction(
                        record.source_namespace, record.source_record_id,
                        record.file_sha256, action, reasons,
                    ))
                if apply:
                    await session.commit()
        except Exception:
            if apply:
                await _clean_failed_batch(session_factory, root, created_names)
            raise
        if progress:
            progress(start // batch_size + 1, total_batches)
    return IntakeReport(applied=apply, actions=tuple(actions))


async def import_records(
    session_factory: async_sessionmaker[AsyncSession],
    records: tuple[PreparedIntake, ...],
    *,
    apply: bool = False,
    batch_size: int = 200,
    progress: Callable[[int, int], None] | None = None,
) -> IntakeReport:
    root = private_intake_dir(create=apply)
    if apply:
        with _storage_lock(root):
            return await _import_records_impl(
                session_factory, records, root,
                apply=True, batch_size=batch_size, progress=progress,
            )
    return await _import_records_impl(
        session_factory, records, root,
        apply=False, batch_size=batch_size, progress=progress,
    )


async def _run_cli(
    records: tuple[PreparedIntake, ...], *, apply: bool, batch_size: int
) -> IntakeReport:
    from app.db.session import engine

    factory = async_sessionmaker(engine, expire_on_commit=False)
    return await import_records(
        factory, records, apply=apply, batch_size=batch_size,
        progress=lambda current, total: print(
            f"[leaflet-intake] 批次 {current}/{total} 完成", flush=True
        ),
    )


async def _audit_cli(*, remove: bool) -> tuple[str, ...]:
    from app.db.session import engine

    return await audit_orphans(
        async_sessionmaker(engine, expire_on_commit=False), remove=remove
    )


def _private_report_path(path: Path, *, apply: bool) -> Path:
    resolved = path.expanduser().resolve()
    public = configured_storage_path(settings.UPLOAD_DIR)
    if resolved == public or resolved.is_relative_to(public):
        raise IntakeManifestError("报告不能位于公开 UPLOAD_DIR")
    if apply:
        private = private_intake_dir(create=False)
        if not resolved.is_relative_to(private) or resolved == private:
            raise IntakeManifestError("apply 报告必须位于待核私有目录内")
    if resolved.suffix.lower() != ".json":
        raise IntakeManifestError("报告文件必须是 .json")
    if resolved.exists() or resolved.is_symlink():
        raise FileExistsError(f"报告已存在，不覆盖：{resolved}")
    return resolved


def _write_report(path: Path, payload: dict) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".report-", suffix=".json",
            dir=path.parent, delete=False,
        ) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="严格离线 JSON 清单")
    parser.add_argument("--source-root", type=Path, help="本地 PDF 原件根目录")
    parser.add_argument("--batch-size", type=int, default=200, help="每批 1-300 条")
    parser.add_argument("--apply", action="store_true", help="显式写入私有待核区")
    parser.add_argument("--report", type=Path, help="对账 JSON；apply 时必需且须在待核私有目录")
    parser.add_argument("--audit-orphans", action="store_true", help="对账待核私有 PDF")
    parser.add_argument("--remove-orphans", action="store_true", help="清理无数据库引用的待核 PDF")
    args = parser.parse_args(argv)
    if args.remove_orphans and not args.audit_orphans:
        parser.error("--remove-orphans 必须与 --audit-orphans 同用")
    if args.audit_orphans:
        if args.manifest or args.source_root or args.apply or args.report:
            parser.error("孤儿对账不能与清单导入同用")
        try:
            orphans = asyncio.run(_audit_cli(remove=args.remove_orphans))
            print(f"[leaflet-intake] 待核目录孤儿文件 {len(orphans)} 个")
            for name in orphans[:20]:
                print(f"  {name}")
            return 0 if args.remove_orphans or not orphans else 2
        except Exception as exc:
            print(f"[leaflet-intake] 孤儿对账失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    if not args.manifest or not args.source_root:
        parser.error("导入时必须同时提供 --manifest 和 --source-root")
    if args.apply and not args.report:
        parser.error("--apply 必须指定私有 --report")

    report_path: Path | None = None
    try:
        records = load_manifest(args.manifest, args.source_root)
        if args.report:
            report_path = _private_report_path(args.report, apply=args.apply)
            report_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if args.apply:
                report_path.parent.chmod(0o700)
            descriptor = os.open(
                report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({
                    "state": "started",
                    "manifest_sha256": records[0].manifest_sha256,
                }, output, ensure_ascii=False)
                output.write("\n")
        try:
            report = asyncio.run(_run_cli(
                records, apply=args.apply, batch_size=args.batch_size
            ))
        except Exception as exc:
            if report_path:
                _write_report(report_path, {
                    "state": "failed",
                    "manifest_sha256": records[0].manifest_sha256,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "note": "前序批次可能已提交；检查数据库和孤儿文件后重跑。",
                })
            raise
        totals = {action: report.count(action) for action in (
            "insert", "unchanged", "conflict",
        )}
        review_totals: dict[str, int] = {}
        for row in report.actions:
            for reason in row.review_reasons:
                review_totals[reason] = review_totals.get(reason, 0) + 1
        if report_path:
            _write_report(report_path, {
                "state": "completed",
                "manifest_sha256": records[0].manifest_sha256,
                "applied": report.applied,
                "totals": totals,
                "review_reason_totals": review_totals,
                "actions": [row.__dict__ for row in report.actions],
            })
        print(
            f"[leaflet-intake] {'已暂存' if report.applied else '仅预览'} "
            f"{len(records)} 条；{totals}；待人工核验 {review_totals}。"
        )
        return 2 if totals["conflict"] else 0
    except Exception as exc:
        print(f"[leaflet-intake] 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
