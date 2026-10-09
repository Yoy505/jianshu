"""将已获准使用的本地药品说明书 PDF 暂存为待审核原件。

脚本不联网、不解析临床内容、不修改 mp_drugs，也不提供发布操作。
默认 dry-run；--apply 才写入私有存储和数据库。可在中断后重跑，按
(drug_id, PDF SHA-256) 幂等跳过已暂存原件。
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
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.core.storage_paths import configured_storage_path  # noqa: E402
from app.models import MpDrug, MpDrugLeaflet  # noqa: E402


MAX_PDF_BYTES = 30 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MANIFEST_FIELDS = frozenset({"schema_version", "dataset_name", "records"})
RECORD_FIELDS = frozenset({
    "approval_no", "pdf_path", "source_name", "source_document_id",
    "source_url", "authorization_ref", "document_date", "document_version",
    "source_checked_at", "expected_sha256",
})


class LeafletManifestError(ValueError):
    """本地清单或 PDF 不符合暂存契约。"""


@dataclass(frozen=True)
class PreparedLeaflet:
    approval_no: str
    pdf_path: Path
    original_filename: str
    source_name: str
    source_document_id: str
    source_url: str | None
    authorization_ref: str
    document_date: date | None
    document_version: str | None
    source_checked_at: date
    file_size: int
    file_sha256: str
    manifest_sha256: str


@dataclass(frozen=True)
class ImportAction:
    approval_no: str
    source_document_id: str
    file_sha256: str
    action: str  # insert / unchanged / identity_backfill / manual_review / identity_mismatch / unmatched / demo_blocked


@dataclass(frozen=True)
class ImportReport:
    applied: bool
    actions: tuple[ImportAction, ...]

    def count(self, action: str) -> int:
        return sum(row.action == action for row in self.actions)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise LeafletManifestError(f"JSON 字段重复：{key}")
        value[key] = item
    return value


def _required_text(record: dict, field: str, max_length: int) -> str:
    value = record[field]
    if not isinstance(value, str) or not value or value != value.strip():
        raise LeafletManifestError(f"{field} 必须是非空且无首尾空格的字符串")
    if len(value) > max_length:
        raise LeafletManifestError(f"{field} 超过 {max_length} 字符")
    return value


def _optional_text(record: dict, field: str, max_length: int) -> str | None:
    return None if record[field] is None else _required_text(record, field, max_length)


def _iso_date(value: object, field: str, *, optional: bool = False) -> date | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise LeafletManifestError(f"{field} 必须是 YYYY-MM-DD 日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LeafletManifestError(f"{field} 不是有效日期") from exc


def _validate_source_url(value: str | None) -> None:
    if value is None:
        return
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username
        or parsed.password or parsed.fragment or any(ord(c) < 32 for c in value)
    ):
        raise LeafletManifestError("source_url 只能是无凭据的 HTTPS 链接或 null")


def _pdf_source_path(source_root: Path, relative_name: str) -> Path:
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".pdf":
        raise LeafletManifestError("pdf_path 必须是 source-root 下的相对 PDF 路径")
    candidate = source_root / relative
    if candidate.is_symlink():
        raise LeafletManifestError(f"不接受符号链接原件：{relative_name}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(source_root) or not resolved.is_file():
        raise LeafletManifestError(f"找不到 source-root 内的 PDF：{relative_name}")
    if len(candidate.name) > 255:
        raise LeafletManifestError(f"PDF 文件名过长：{relative_name}")
    return resolved


def _identity_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _pdf_identity_status(path: Path, approval_no: str, common_name: str) -> str:
    """机械核对 PDF 正文标识，不等于医学审核；扫描件交人工处理。"""
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
            return "manual_review"
        pieces: list[str] = []
        total_chars = 0
        for page in reader.pages:
            value = page.extract_text() or ""
            total_chars += len(value)
            if total_chars > 300_000:
                return "manual_review"
            pieces.append(value)
    except Exception:
        # 解析异常不能降级为“已匹配”；保留原件供单独人工流程处理。
        return "manual_review"
    compact = _identity_text("".join(pieces))
    if len(compact) < 100:
        return "manual_review"
    if _identity_text(approval_no) in compact and _identity_text(common_name) in compact:
        return "text_match"
    return "identity_mismatch"


def _hash_valid_pdf(path: Path) -> tuple[str, int]:
    size = path.stat().st_size
    if not 32 <= size <= MAX_PDF_BYTES:
        raise LeafletManifestError(f"PDF 大小须在 32 字节至 30 MiB：{path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise LeafletManifestError(f"不是 PDF 原件：{path.name}")
        source.seek(0)
        read_size = 0
        while chunk := source.read(CHUNK_BYTES):
            read_size += len(chunk)
            digest.update(chunk)
        if read_size != size:
            raise LeafletManifestError(f"PDF 在核验时发生变化：{path.name}")
        source.seek(max(0, size - 2048))
        if b"%%EOF" not in source.read():
            raise LeafletManifestError(f"PDF 缺少结束标记：{path.name}")
    return digest.hexdigest(), size


def load_manifest(manifest_path: Path, source_root: Path) -> tuple[PreparedLeaflet, ...]:
    """全量预检本地清单/原件；任何错误发生在首次数据库写入之前。"""
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise LeafletManifestError(f"source-root 不是本地目录：{source_root}")
    public_root = configured_storage_path(settings.UPLOAD_DIR)
    if source_root == public_root or source_root.is_relative_to(public_root):
        raise LeafletManifestError("source-root 不能位于公开 UPLOAD_DIR 内")
    try:
        raw = manifest_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LeafletManifestError(f"无法读取清单：{exc}") from exc
    if not isinstance(payload, dict) or set(payload) != MANIFEST_FIELDS:
        raise LeafletManifestError("清单顶层字段不符合契约")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise LeafletManifestError("schema_version 必须为 1")
    _required_text(payload, "dataset_name", 200)
    rows = payload["records"]
    if not isinstance(rows, list) or not rows:
        raise LeafletManifestError("records 必须是非空数组")

    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    records: list[PreparedLeaflet] = []
    seen_source: set[tuple[str, str]] = set()
    seen_content: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != RECORD_FIELDS:
            raise LeafletManifestError(f"第 {index} 条字段不符合说明书清单契约")
        approval_no = _required_text(row, "approval_no", 50)
        source_document_id = _required_text(row, "source_document_id", 200)
        if (approval_no, source_document_id) in seen_source:
            raise LeafletManifestError(f"同一批准文号和来源文档 ID 重复：{approval_no}")
        seen_source.add((approval_no, source_document_id))
        source_url = _optional_text(row, "source_url", 1000)
        _validate_source_url(source_url)
        relative_name = _required_text(row, "pdf_path", 500)
        pdf_path = _pdf_source_path(source_root, relative_name)
        file_sha256, file_size = _hash_valid_pdf(pdf_path)
        expected_sha256 = _required_text(row, "expected_sha256", 64)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise LeafletManifestError(f"{approval_no} 的 expected_sha256 必须是 64 位小写十六进制")
        if expected_sha256 != file_sha256:
            raise LeafletManifestError(f"{approval_no} 的 PDF SHA-256 与清单不一致")
        if (approval_no, file_sha256) in seen_content:
            raise LeafletManifestError(f"同一批准文号的 PDF 内容重复：{approval_no}")
        seen_content.add((approval_no, file_sha256))
        authorization_ref = _required_text(row, "authorization_ref", 200)
        if authorization_ref.casefold() in {"tbd", "unknown", "pending", "待确认", "暂无"}:
            raise LeafletManifestError(f"{approval_no} 缺少明确的使用依据编号")
        records.append(PreparedLeaflet(
            approval_no=approval_no,
            pdf_path=pdf_path,
            original_filename=pdf_path.name,
            source_name=_required_text(row, "source_name", 200),
            source_document_id=source_document_id,
            source_url=source_url,
            authorization_ref=authorization_ref,
            document_date=_iso_date(row["document_date"], "document_date", optional=True),
            document_version=_optional_text(row, "document_version", 100),
            source_checked_at=_iso_date(row["source_checked_at"], "source_checked_at"),
            file_size=file_size,
            file_sha256=file_sha256,
            manifest_sha256=manifest_sha256,
        ))
    return tuple(records)


def private_leaflet_dir(*, create: bool) -> Path:
    root = configured_storage_path(settings.DRUG_LEAFLET_DIR)
    public = configured_storage_path(settings.UPLOAD_DIR)
    if root == public or root.is_relative_to(public):
        raise RuntimeError("药品说明书私有目录不能位于公开 UPLOAD_DIR 内")
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
    return root


@contextmanager
def _storage_lock(root: Path):
    """序列化此脚本的写入和孤儿清理，避免并行导入互相误删。"""
    lock_path = root / ".import.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _store_pdf(record: PreparedLeaflet, root: Path) -> tuple[str, bool]:
    """重新校验拷贝的原件，按内容哈希原子落盘；断点重跑可安全复用。"""
    name = f"{record.file_sha256}.pdf"
    target = root / name
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise RuntimeError(f"私有存储目标异常：{name}")
        saved_hash, saved_size = _hash_valid_pdf(target)
        if (saved_hash, saved_size) != (record.file_sha256, record.file_size):
            raise RuntimeError(f"私有存储哈希冲突或文件损坏：{name}")
        return name, False

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".incoming-", suffix=".pdf", dir=root, delete=False) as target_file:
            temporary_path = Path(target_file.name)
            os.chmod(temporary_path, 0o600)
            digest = hashlib.sha256()
            copied = 0
            with record.pdf_path.open("rb") as source:
                while chunk := source.read(CHUNK_BYTES):
                    copied += len(chunk)
                    if copied > MAX_PDF_BYTES:
                        raise LeafletManifestError("PDF 在预检后被修改且超过大小限制")
                    digest.update(chunk)
                    target_file.write(chunk)
            target_file.flush()
            os.fsync(target_file.fileno())
        if (digest.hexdigest(), copied) != (record.file_sha256, record.file_size):
            raise LeafletManifestError("PDF 在预检后发生变化，已停止导入")
        try:
            os.link(temporary_path, target)
            created = True
        except FileExistsError:
            created = False
            saved_hash, saved_size = _hash_valid_pdf(target)
            if (saved_hash, saved_size) != (record.file_sha256, record.file_size):
                raise RuntimeError(f"私有存储哈希冲突或文件损坏：{name}")
        return name, created
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def _clean_failed_batch(
    session_factory: async_sessionmaker[AsyncSession], root: Path, created_names: set[str]
) -> None:
    """提交结果未知时先查持久引用，只删除本批新建且无人引用的原件。"""
    if not created_names:
        return
    try:
        async with session_factory() as session:
            referenced = set((await session.scalars(
                select(MpDrugLeaflet.stored_filename).where(
                    MpDrugLeaflet.stored_filename.in_(created_names)
                )
            )).all())
    except Exception as exc:
        raise RuntimeError(
            "批次失败且数据库暂不可用于检查孤儿文件；请恢复数据库后运行 --audit-orphans"
        ) from exc
    for name in created_names - referenced:
        path = root / name
        if path.is_symlink():
            raise RuntimeError(f"待清理文件是符号链接，请人工检查：{name}")
        path.unlink(missing_ok=True)


async def audit_orphans(
    session_factory: async_sessionmaker[AsyncSession], *, remove: bool = False
) -> tuple[str, ...]:
    """对账私有哈希 PDF 与数据库；清理须显式指定 remove 且暂停其他写入方。"""
    root = private_leaflet_dir(create=False)
    if not root.exists():
        return ()
    with _storage_lock(root):
        files = {
            path.name for path in root.iterdir()
            if re.fullmatch(r"[0-9a-f]{64}\.pdf", path.name) and path.is_file()
        }
        async with session_factory() as session:
            referenced = set((await session.scalars(
                select(MpDrugLeaflet.stored_filename)
            )).all())
        orphans = tuple(sorted(files - referenced))
        if remove:
            for name in orphans:
                path = root / name
                if path.is_symlink():
                    raise RuntimeError(f"待清理文件是符号链接，请人工检查：{name}")
                stored_hash, _ = _hash_valid_pdf(path)
                if stored_hash != name[:-4]:
                    raise RuntimeError(f"待清理文件内容与哈希文件名不一致：{name}")
                path.unlink()
        return orphans


async def _import_records_impl(
    session_factory: async_sessionmaker[AsyncSession],
    records: tuple[PreparedLeaflet, ...],
    root: Path,
    *,
    apply: bool = False,
    batch_size: int = 200,
    progress: Callable[[int, int], None] | None = None,
) -> ImportReport:
    """每批独立提交；失败后重跑会跳过已提交的相同 PDF。"""
    if not 1 <= batch_size <= 500:
        raise ValueError("batch_size 必须在 1 到 500 之间")
    actions: list[ImportAction] = []
    total_batches = (len(records) + batch_size - 1) // batch_size
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        created_names: set[str] = set()
        try:
            async with session_factory() as session:
                approval_numbers = {record.approval_no for record in batch}
                drugs = (
                    await session.execute(select(MpDrug).where(MpDrug.approval_no.in_(approval_numbers)))
                ).scalars().all()
                drug_by_approval = {drug.approval_no: drug for drug in drugs}
                drug_ids = [drug.id for drug in drugs]
                existing = (
                    (await session.execute(
                        select(MpDrugLeaflet).where(MpDrugLeaflet.drug_id.in_(drug_ids))
                    )).scalars().all()
                    if drug_ids else []
                )
                existing_by_key = {(row.drug_id, row.file_sha256): row for row in existing}
                for record in batch:
                    drug = drug_by_approval.get(record.approval_no)
                    if drug is None:
                        action = "unmatched"
                    elif drug.publication_status == "demo":
                        action = "demo_blocked"
                    else:
                        identity = _pdf_identity_status(
                            record.pdf_path, record.approval_no, drug.common_name
                        )
                        if identity != "text_match":
                            actions.append(ImportAction(
                                record.approval_no, record.source_document_id,
                                record.file_sha256, identity,
                            ))
                            continue
                        if (drug.id, record.file_sha256) in existing_by_key:
                            previous = existing_by_key[(drug.id, record.file_sha256)]
                            if previous.identity_check_method != "pypdf_text_match":
                                if previous.status != "pending":
                                    action = "protected_unverified"
                                else:
                                    action = "identity_backfill"
                                    if apply:
                                        previous.identity_check_method = "pypdf_text_match"
                            else:
                                action = "unchanged"
                            if apply:
                                if previous.stored_filename != f"{record.file_sha256}.pdf":
                                    raise RuntimeError("既有说明书存储路径不符合哈希命名约定")
                                _store_pdf(record, root)
                        else:
                            action = "insert"
                            if apply:
                                stored_filename, created = _store_pdf(record, root)
                                if created:
                                    created_names.add(stored_filename)
                                session.add(MpDrugLeaflet(
                                    drug_id=drug.id,
                                    approval_no=record.approval_no,
                                    source_name=record.source_name,
                                    source_document_id=record.source_document_id,
                                    source_url=record.source_url,
                                    authorization_ref=record.authorization_ref,
                                    document_date=record.document_date,
                                    document_version=record.document_version,
                                    source_checked_at=record.source_checked_at,
                                    original_filename=record.original_filename,
                                    file_size=record.file_size,
                                    file_sha256=record.file_sha256,
                                    stored_filename=stored_filename,
                                    manifest_sha256=record.manifest_sha256,
                                    identity_check_method="pypdf_text_match",
                                    status="pending",
                                ))
                    actions.append(ImportAction(
                        record.approval_no, record.source_document_id, record.file_sha256, action
                    ))
                if apply:
                    await session.commit()
        except Exception:
            if apply:
                await _clean_failed_batch(session_factory, root, created_names)
            raise
        if progress:
            progress(start // batch_size + 1, total_batches)
    return ImportReport(applied=apply, actions=tuple(actions))


async def import_records(
    session_factory: async_sessionmaker[AsyncSession],
    records: tuple[PreparedLeaflet, ...],
    *,
    apply: bool = False,
    batch_size: int = 200,
    progress: Callable[[int, int], None] | None = None,
) -> ImportReport:
    root = private_leaflet_dir(create=apply)
    if apply:
        with _storage_lock(root):
            return await _import_records_impl(
                session_factory, records, root, apply=True,
                batch_size=batch_size, progress=progress,
            )
    return await _import_records_impl(
        session_factory, records, root, batch_size=batch_size, progress=progress,
    )


async def _run_cli(
    records: tuple[PreparedLeaflet, ...], *, apply: bool, batch_size: int
) -> ImportReport:
    from app.db.session import engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return await import_records(
        session_factory, records, apply=apply, batch_size=batch_size,
        progress=lambda number, total: print(f"[leaflets] 批次 {number}/{total} 完成", flush=True),
    )


async def _audit_cli(*, remove: bool) -> tuple[str, ...]:
    from app.db.session import engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return await audit_orphans(session_factory, remove=remove)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="严格 JSON 清单")
    parser.add_argument("--source-root", type=Path, help="已获准使用的本地 PDF 根目录")
    parser.add_argument("--batch-size", type=int, default=200, help="每批 1-500 条，默认 200")
    parser.add_argument("--apply", action="store_true", help="显式写入私有暂存区；默认只预览")
    parser.add_argument("--report", type=Path, help="完整对账 JSON 输出路径")
    parser.add_argument("--audit-orphans", action="store_true", help="仅对账私有 PDF 与数据库引用")
    parser.add_argument("--remove-orphans", action="store_true", help="与 --audit-orphans 同用，清理无引用的哈希 PDF")
    args = parser.parse_args(argv)
    if args.remove_orphans and not args.audit_orphans:
        parser.error("--remove-orphans 必须与 --audit-orphans 同用")
    if args.audit_orphans:
        if args.manifest or args.source_root or args.apply or args.report:
            parser.error("孤儿对账不能同时执行清单导入")
        try:
            orphans = asyncio.run(_audit_cli(remove=args.remove_orphans))
            verb = "已清理" if args.remove_orphans else "发现"
            print(f"[leaflets] {verb} {len(orphans)} 个无数据库引用的私有 PDF")
            for name in orphans[:20]:
                print(f"  {name}")
            return 0 if args.remove_orphans or not orphans else 2
        except Exception as exc:
            print(f"[leaflets] 孤儿对账失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    if not args.manifest or not args.source_root:
        parser.error("导入时必须同时提供 --manifest 与 --source-root")
    try:
        report_path = args.report.expanduser().resolve() if args.report else None
        if report_path:
            if report_path.is_relative_to(configured_storage_path(settings.UPLOAD_DIR)):
                raise LeafletManifestError("对账报告不能写入公开 UPLOAD_DIR")
            if report_path.exists():
                raise FileExistsError(f"对账报告已存在，不覆盖：{args.report}")
        records = load_manifest(args.manifest, args.source_root)
        report = asyncio.run(_run_cli(records, apply=args.apply, batch_size=args.batch_size))
        totals = {action: report.count(action) for action in (
            "insert", "unchanged", "identity_backfill", "manual_review",
            "identity_mismatch", "unmatched", "demo_blocked", "protected_unverified",
        )}
        if report_path:
            if report_path.exists():
                raise FileExistsError(f"对账报告已存在，不覆盖：{report_path}")
            report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({
                    "manifest_sha256": records[0].manifest_sha256,
                    "applied": report.applied,
                    "totals": totals,
                    "actions": [row.__dict__ for row in report.actions],
                }, output, ensure_ascii=False, indent=2)
                output.write("\n")
        mode = "已暂存" if report.applied else "仅预览"
        print(f"[leaflets] {mode} {len(records)} 条；{totals}。新增版本均为 pending。")
        exceptions = [row for row in report.actions if row.action in {
            "manual_review", "identity_mismatch", "unmatched", "demo_blocked", "protected_unverified",
        }]
        for row in exceptions[:20]:
            print(f"  需处理：{row.approval_no} / {row.source_document_id} / {row.action}")
        if len(exceptions) > 20:
            print(f"  其余 {len(exceptions) - 20} 条请查看 --report 完整对账")
        return 2 if any(totals[action] for action in (
            "manual_review", "identity_mismatch", "unmatched", "demo_blocked",
            "protected_unverified",
        )) else 0
    except Exception as exc:
        print(f"[leaflets] 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
