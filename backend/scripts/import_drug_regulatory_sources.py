"""离线暂存药品监管来源记录；不导入说明书，也不创建或发布药品。

清单和来源原件先全量预检。默认 dry-run；--apply 才按批写入独立待审表。
同一来源键的变化只报告 conflict，不覆盖先前证据或人工关联。
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import heapq
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.core.storage_paths import configured_storage_path  # noqa: E402
from app.models import MpDrug, MpDrugRegulatoryObservation, MpDrugRegulatorySource  # noqa: E402


MAX_SOURCE_BYTES = 500 * 1024 * 1024
DOCUMENT_CHUNK_BYTES = 1024 * 1024
MANIFEST_FIELDS = frozenset({"schema_version", "dataset_name", "records"})
RECORD_FIELDS = frozenset({
    "source_namespace", "source_record_id", "registration_identifier",
    "source_product_name", "drug_class", "origin", "regulatory_status",
    "source_status_text", "regulatory_as_of", "source_url",
    "source_document_path", "source_document_sha256", "source_checked_at",
})
DRUG_CLASSES = frozenset({"chemical", "tcm", "biologic"})
ORIGINS = frozenset({"domestic", "imported"})
REGULATORY_STATUSES = frozenset({
    "approved_current", "revoked", "expired", "suspended", "unknown",
})
# 仅有“已批准”无法证明现行有效。此表可以以后经来源核实后保守扩充。
EXPLICIT_CURRENT_STATUS_TEXTS = frozenset({"有效", "现行有效", "有效期内"})
SOURCE_HOSTS = frozenset({
    "www.nmpa.gov.cn", "app1.nmpa.gov.cn", "www.cde.org.cn",
    "app.gjzwfw.gov.cn",
})


class RegulatoryManifestError(ValueError):
    """离线来源清单、字段或原件未通过预检。"""


@dataclass(frozen=True)
class RegulatoryRecord:
    source_namespace: str
    source_record_id: str
    registration_identifier: str | None
    source_product_name: str
    drug_class: str
    origin: str
    regulatory_status: str
    source_status_text: str | None
    regulatory_as_of: date | None
    source_url: str
    source_document_path: Path
    source_document_name: str
    source_document_sha256: str
    source_checked_at: date
    manifest_sha256: str

    @property
    def source_key(self) -> tuple[str, str]:
        return self.source_namespace, self.source_record_id

    @property
    def stored_document_key(self) -> str:
        return f"{self.source_document_sha256}.blob"


@dataclass(frozen=True)
class ImportAction:
    source_namespace: str
    source_record_id: str
    registration_identifier: str | None
    drug_class: str
    origin: str
    regulatory_status: str
    source_document_sha256: str
    source_document_name: str
    action: str  # insert / unchanged / conflict
    canonical_candidate_id: int | None
    duplicate_identifier_count: int
    duplicate_identifier_examples: tuple[tuple[str, str], ...]

    @property
    def unmatched(self) -> bool:
        return self.canonical_candidate_id is None


@dataclass(frozen=True)
class ImportReport:
    applied: bool
    actions: tuple[ImportAction, ...]

    def count(self, action: str) -> int:
        return sum(row.action == action for row in self.actions)

    @property
    def strata(self) -> dict[str, int]:
        counts = Counter(
            f"{row.drug_class}/{row.origin}/{row.regulatory_status}"
            for row in self.actions
        )
        return dict(sorted(counts.items()))

    @property
    def strata_by_action(self) -> dict[str, dict[str, int]]:
        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for row in self.actions:
            key = f"{row.drug_class}/{row.origin}/{row.regulatory_status}"
            counts[row.action][key] += 1
        return {
            action: dict(sorted(strata.items()))
            for action, strata in sorted(counts.items())
        }

    @property
    def by_source_namespace(self) -> dict[str, int]:
        return dict(sorted(Counter(row.source_namespace for row in self.actions).items()))

    @property
    def unmatched_count(self) -> int:
        return sum(row.unmatched for row in self.actions)

    @property
    def duplicate_identifier_count(self) -> int:
        return sum(row.duplicate_identifier_count > 0 for row in self.actions)


def _public_upload_dir() -> Path:
    return configured_storage_path(settings.UPLOAD_DIR)


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise RegulatoryManifestError(f"JSON 字段重复：{key}")
        result[key] = value
    return result


def _required_text(row: dict, field: str, max_length: int) -> str:
    value = row[field]
    if not isinstance(value, str) or not value or value != value.strip():
        raise RegulatoryManifestError(f"{field} 必须是非空且无首尾空格的字符串")
    if len(value) > max_length or any(ord(char) < 32 for char in value):
        raise RegulatoryManifestError(f"{field} 超长或含控制字符")
    return value


def _optional_text(row: dict, field: str, max_length: int) -> str | None:
    return None if row[field] is None else _required_text(row, field, max_length)


def _iso_date(value: object, field: str, *, optional: bool = False) -> date | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise RegulatoryManifestError(f"{field} 必须是 YYYY-MM-DD 日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RegulatoryManifestError(f"{field} 不是有效日期") from exc


def _source_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise RegulatoryManifestError("source_url 端口无效") from exc
    if (
        parsed.scheme != "https" or host not in SOURCE_HOSTS or parsed.username
        or parsed.password or port not in (None, 443) or parsed.fragment
        or not parsed.path or any(ord(char) < 32 for char in value)
    ):
        raise RegulatoryManifestError("source_url 不在已核实的监管查询 HTTPS 主机白名单中")
    return value


def _source_document(source_root: Path, relative_name: str) -> Path:
    relative = Path(relative_name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RegulatoryManifestError("source_document_path 必须是 source-root 内相对路径")
    candidate = source_root / relative
    cursor = source_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RegulatoryManifestError("来源原件路径不可经过符号链接")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(source_root) or not resolved.is_file():
        raise RegulatoryManifestError(f"找不到 source-root 内来源原件：{relative_name}")
    public_root = _public_upload_dir()
    if resolved == public_root or resolved.is_relative_to(public_root):
        raise RegulatoryManifestError("来源原件不能位于公开 UPLOAD_DIR 内")
    return resolved


def _sha256_file(path: Path) -> str:
    if not 1 <= path.stat().st_size <= MAX_SOURCE_BYTES:
        raise RegulatoryManifestError(f"来源原件须在 1 字节至 500 MiB：{path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(DOCUMENT_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(manifest_path: Path, source_root: Path) -> tuple[RegulatoryRecord, ...]:
    """逐条验证清单与本地来源原件；不将字段内容当成已人工核准。"""
    source_root = source_root.expanduser().resolve()
    if not source_root.is_dir():
        raise RegulatoryManifestError(f"source-root 不是本地目录：{source_root}")
    public_root = _public_upload_dir()
    if source_root == public_root or source_root.is_relative_to(public_root):
        raise RegulatoryManifestError("source-root 不能位于公开 UPLOAD_DIR 内")
    try:
        raw = manifest_path.expanduser().read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegulatoryManifestError(f"无法读取 JSON 清单：{exc}") from exc
    if not isinstance(payload, dict) or set(payload) != MANIFEST_FIELDS:
        raise RegulatoryManifestError("清单顶层字段不符合契约")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise RegulatoryManifestError("schema_version 必须为 1")
    _required_text(payload, "dataset_name", 200)
    rows = payload["records"]
    if not isinstance(rows, list) or not rows:
        raise RegulatoryManifestError("records 必须是非空数组")

    manifest_hash = hashlib.sha256(raw).hexdigest()
    document_hash_cache: dict[Path, str] = {}
    seen_keys: set[tuple[str, str]] = set()
    records: list[RegulatoryRecord] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != RECORD_FIELDS:
            raise RegulatoryManifestError(f"第 {index} 条字段不符合监管来源契约")
        namespace = _required_text(row, "source_namespace", 64)
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,63}", namespace):
            raise RegulatoryManifestError(f"第 {index} 条 source_namespace 格式错误")
        source_id = _required_text(row, "source_record_id", 200)
        key = (namespace, source_id)
        if key in seen_keys:
            raise RegulatoryManifestError(f"来源键重复：{namespace}/{source_id}")
        seen_keys.add(key)
        drug_class = _required_text(row, "drug_class", 16)
        origin = _required_text(row, "origin", 16)
        status = _required_text(row, "regulatory_status", 24)
        if drug_class not in DRUG_CLASSES or origin not in ORIGINS:
            raise RegulatoryManifestError(f"第 {index} 条药品类别或国产/进口类别不受支持")
        if status not in REGULATORY_STATUSES:
            raise RegulatoryManifestError(f"第 {index} 条监管状态未知：{status}")
        raw_status = _optional_text(row, "source_status_text", 200)
        as_of = _iso_date(row["regulatory_as_of"], "regulatory_as_of", optional=True)
        checked_at = _iso_date(row["source_checked_at"], "source_checked_at")
        if as_of is not None and as_of > checked_at:
            raise RegulatoryManifestError(f"第 {index} 条监管状态日期晚于来源核验日期")
        if status == "approved_current":
            if raw_status not in EXPLICIT_CURRENT_STATUS_TEXTS or as_of is None:
                raise RegulatoryManifestError(
                    f"第 {index} 条 approved_current 缺少明确现行有效原文与日期"
                )
        elif status != "unknown" and (raw_status is None or as_of is None):
            raise RegulatoryManifestError(f"第 {index} 条已知监管状态缺少来源原文与日期")
        relative_name = _required_text(row, "source_document_path", 500)
        document_path = _source_document(source_root, relative_name)
        expected_hash = _required_text(row, "source_document_sha256", 64)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise RegulatoryManifestError(f"第 {index} 条来源原件 SHA-256 格式错误")
        if document_path not in document_hash_cache:
            document_hash_cache[document_path] = _sha256_file(document_path)
        if document_hash_cache[document_path] != expected_hash:
            raise RegulatoryManifestError(f"第 {index} 条来源原件 SHA-256 不匹配")
        records.append(RegulatoryRecord(
            source_namespace=namespace,
            source_record_id=source_id,
            registration_identifier=_optional_text(row, "registration_identifier", 100),
            source_product_name=_required_text(row, "source_product_name", 200),
            drug_class=drug_class,
            origin=origin,
            regulatory_status=status,
            source_status_text=raw_status,
            regulatory_as_of=as_of,
            source_url=_source_url(_required_text(row, "source_url", 1000)),
            source_document_path=document_path,
            source_document_name=_required_text(
                {"source_document_name": document_path.name}, "source_document_name", 255
            ),
            source_document_sha256=expected_hash,
            source_checked_at=checked_at,
            manifest_sha256=manifest_hash,
        ))
    return tuple(records)


def _fact_values(record: RegulatoryRecord) -> dict:
    return {
        "registration_identifier": record.registration_identifier,
        "source_product_name": record.source_product_name,
        "drug_class": record.drug_class,
        "origin": record.origin,
        "regulatory_status": record.regulatory_status,
        "source_status_text": record.source_status_text,
        "regulatory_as_of": record.regulatory_as_of,
    }


def _fact_payload(record: RegulatoryRecord) -> dict:
    facts = _fact_values(record)
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in facts.items()
    }


def _fact_sha256(record: RegulatoryRecord) -> str:
    raw = json.dumps(
        _fact_payload(record), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _observation(
    record: RegulatoryRecord, source_id: int, *, kind: str
) -> MpDrugRegulatoryObservation:
    return MpDrugRegulatoryObservation(
        source_id=source_id,
        source_document_sha256=record.source_document_sha256,
        stored_document_key=record.stored_document_key,
        source_document_name=record.source_document_name,
        source_url=record.source_url,
        fact_sha256=_fact_sha256(record),
        reported_facts=_fact_payload(record),
        kind=kind,
        manifest_sha256=record.manifest_sha256,
        source_checked_at=record.source_checked_at,
        review_status="pending",
    )


def private_source_dir(*, create: bool) -> Path:
    root = configured_storage_path(settings.DRUG_REGULATORY_SOURCE_DIR)
    public = _public_upload_dir()
    if root == public or root.is_relative_to(public):
        raise RegulatoryManifestError("监管来源私有目录不能位于公开 UPLOAD_DIR 内")
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
    return root


@contextmanager
def _storage_lock(root: Path):
    descriptor = os.open(root / ".import.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _store_document(
    record: RegulatoryRecord, root: Path, verified_keys: set[str]
) -> tuple[str, bool]:
    """按 SHA-256 私有留存原件；同一文件在一次运行中只核验/复制一次。"""
    key = record.stored_document_key
    if key in verified_keys:
        return key, False
    target = root / key
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or _sha256_file(target) != record.source_document_sha256:
            raise RuntimeError(f"私有来源原件损坏或目标异常：{key}")
        verified_keys.add(key)
        return key, False

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".incoming-", suffix=".blob", dir=root, delete=False
        ) as output:
            temporary_path = Path(output.name)
            os.chmod(temporary_path, 0o600)
            digest = hashlib.sha256()
            copied = 0
            with record.source_document_path.open("rb") as source:
                while chunk := source.read(DOCUMENT_CHUNK_BYTES):
                    copied += len(chunk)
                    if copied > MAX_SOURCE_BYTES:
                        raise RegulatoryManifestError("来源原件预检后增大，已停止复制")
                    output.write(chunk)
                    digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if not copied or digest.hexdigest() != record.source_document_sha256:
            raise RegulatoryManifestError("来源原件预检后发生变化，已停止导入")
        try:
            os.link(temporary_path, target)
            created = True
        except FileExistsError:
            created = False
            if target.is_symlink() or not target.is_file() or _sha256_file(target) != record.source_document_sha256:
                raise RuntimeError(f"私有来源原件损坏或目标异常：{key}")
        verified_keys.add(key)
        return key, created
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def _clean_failed_batch(
    session_factory: async_sessionmaker[AsyncSession], root: Path, created_keys: set[str]
) -> None:
    if not created_keys:
        return
    try:
        async with session_factory() as session:
            referenced = set((await session.scalars(select(
                MpDrugRegulatorySource.stored_document_key
            ).where(MpDrugRegulatorySource.stored_document_key.in_(created_keys)))).all())
            referenced.update((await session.scalars(select(
                MpDrugRegulatoryObservation.stored_document_key
            ).where(MpDrugRegulatoryObservation.stored_document_key.in_(created_keys)))).all())
    except Exception as exc:
        raise RuntimeError(
            "批次失败且数据库暂不可核对私有原件；恢复后运行 --audit-orphans"
        ) from exc
    for key in created_keys - referenced:
        path = root / key
        if path.is_symlink():
            raise RuntimeError(f"待清理来源原件是符号链接：{key}")
        path.unlink(missing_ok=True)


async def audit_orphans(
    session_factory: async_sessionmaker[AsyncSession], *, remove: bool = False
) -> tuple[str, ...]:
    """与数据库引用对账，显式 --remove-orphans 才删除无引用私有原件。"""
    root = private_source_dir(create=False)
    if not root.exists():
        return ()
    with _storage_lock(root):
        files = {
            path.name for path in root.iterdir()
            if re.fullmatch(r"[0-9a-f]{64}\.blob", path.name) and path.is_file()
        }
        async with session_factory() as session:
            referenced = set((await session.scalars(select(
                MpDrugRegulatorySource.stored_document_key
            ))).all())
            referenced.update((await session.scalars(select(
                MpDrugRegulatoryObservation.stored_document_key
            ))).all())
        orphans = tuple(sorted(files - referenced))
        if remove:
            for key in orphans:
                path = root / key
                if path.is_symlink() or _sha256_file(path) != key[:-5]:
                    raise RuntimeError(f"待清理来源原件异常：{key}")
                path.unlink()
        return orphans


async def import_records(
    session_factory: async_sessionmaker[AsyncSession],
    records: tuple[RegulatoryRecord, ...],
    *,
    apply: bool = False,
    batch_size: int = 100,
    progress: Callable[[int, int, tuple[ImportAction, ...]], None] | None = None,
) -> ImportReport:
    """分批暂存。编号重复可共存，来源键变化留给人工处理。"""
    if not 1 <= batch_size <= 200:
        raise ValueError("batch_size 必须在 1 到 200 之间")
    incoming_by_identifier: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for record in records:
        if record.registration_identifier:
            incoming_by_identifier[record.registration_identifier].add(record.source_key)

    actions: list[ImportAction] = []
    existing_keys_cache: dict[str, set[tuple[str, str]]] = {}
    duplicate_stats_cache: dict[str, tuple[int, tuple[tuple[str, str], ...]]] = {}
    candidate_cache: dict[str, int | None] = {}
    verified_storage_keys: set[str] = set()
    root = private_source_dir(create=apply)

    async def run_batches() -> None:
        total_batches = (len(records) + batch_size - 1) // batch_size
        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]
            keys = [record.source_key for record in batch]
            identifiers = {
                record.registration_identifier for record in batch if record.registration_identifier
            }
            created_keys: set[str] = set()
            batch_actions: list[ImportAction] = []
            try:
                async with session_factory() as session:
                    existing = (await session.scalars(select(MpDrugRegulatorySource).where(
                        tuple_(
                            MpDrugRegulatorySource.source_namespace,
                            MpDrugRegulatorySource.source_record_id,
                        ).in_(keys)
                    ))).all()
                    existing_by_key = {
                        (row.source_namespace, row.source_record_id): row for row in existing
                    }
                    existing_observations: dict[
                        tuple[int, str, str, str], MpDrugRegulatoryObservation
                    ] = {}
                    if existing:
                        incoming_hashes = {record.source_document_sha256 for record in batch}
                        observations = (await session.scalars(select(
                            MpDrugRegulatoryObservation
                        ).where(
                            MpDrugRegulatoryObservation.source_id.in_(row.id for row in existing),
                            MpDrugRegulatoryObservation.source_document_sha256.in_(incoming_hashes),
                        ))).all()
                        existing_observations = {
                            (
                                row.source_id, row.source_document_sha256,
                                row.source_url, row.fact_sha256,
                            ): row
                            for row in observations
                        }
                    missing_identifiers = identifiers - existing_keys_cache.keys()
                    if missing_identifiers:
                        for identifier in missing_identifiers:
                            existing_keys_cache[identifier] = set()
                        source_keys = (await session.execute(select(
                            MpDrugRegulatorySource.registration_identifier,
                            MpDrugRegulatorySource.source_namespace,
                            MpDrugRegulatorySource.source_record_id,
                        ).where(
                            MpDrugRegulatorySource.registration_identifier.in_(missing_identifiers)
                        ))).all()
                        for identifier, namespace, source_id in source_keys:
                            existing_keys_cache[identifier].add((namespace, source_id))
                        for identifier in missing_identifiers:
                            combined = incoming_by_identifier[identifier] | existing_keys_cache[identifier]
                            duplicate_stats_cache[identifier] = (
                                len(combined), tuple(heapq.nsmallest(6, combined)),
                            )
                    uncached_candidates = identifiers - candidate_cache.keys()
                    if uncached_candidates:
                        for identifier in uncached_candidates:
                            candidate_cache[identifier] = None
                        candidates = (await session.scalars(select(MpDrug).where(
                            MpDrug.approval_no.in_(uncached_candidates),
                            MpDrug.publication_status != "demo",
                        ))).all()
                        for drug in candidates:
                            candidate_cache[drug.approval_no] = drug.id

                    pending_observations: list[
                        tuple[MpDrugRegulatorySource, RegulatoryRecord, str]
                    ] = []
                    for record in batch:
                        current = existing_by_key.get(record.source_key)
                        facts = _fact_values(record)
                        if current is None:
                            action = "insert"
                            if apply:
                                key, created = _store_document(record, root, verified_storage_keys)
                                if created:
                                    created_keys.add(key)
                                current = MpDrugRegulatorySource(
                                    source_namespace=record.source_namespace,
                                    source_record_id=record.source_record_id,
                                    manifest_sha256=record.manifest_sha256,
                                    source_checked_at=record.source_checked_at,
                                    source_url=record.source_url,
                                    source_document_name=record.source_document_name,
                                    source_document_sha256=record.source_document_sha256,
                                    stored_document_key=record.stored_document_key,
                                    canonical_drug_id=None,
                                    review_status="pending",
                                    **facts,
                                )
                                session.add(current)
                                pending_observations.append((current, record, "matching"))
                        elif any(getattr(current, field) != value for field, value in facts.items()):
                            action = "conflict"
                            if apply:
                                key, created = _store_document(record, root, verified_storage_keys)
                                if created:
                                    created_keys.add(key)
                                current.review_status = "pending"
                                observation = existing_observations.get((
                                    current.id, record.source_document_sha256,
                                    record.source_url, _fact_sha256(record),
                                ))
                                if observation is None:
                                    pending_observations.append((current, record, "conflict"))
                                elif record.source_checked_at > observation.source_checked_at:
                                    observation.source_checked_at = record.source_checked_at
                        elif record.source_checked_at < current.source_checked_at:
                            action = "conflict"
                        else:
                            new_snapshot = (
                                current.source_document_sha256 != record.source_document_sha256
                                or current.source_url != record.source_url
                            )
                            action = (
                                "new_snapshot" if new_snapshot else (
                                    "rechecked" if record.source_checked_at > current.source_checked_at
                                    else "unchanged"
                                )
                            )
                            if apply:
                                key, created = _store_document(record, root, verified_storage_keys)
                                if created:
                                    created_keys.add(key)
                                if new_snapshot:
                                    current.source_url = record.source_url
                                    current.source_document_sha256 = record.source_document_sha256
                                    current.stored_document_key = record.stored_document_key
                                    current.source_document_name = record.source_document_name
                                    current.manifest_sha256 = record.manifest_sha256
                                    current.review_status = "pending"
                                if record.source_checked_at > current.source_checked_at:
                                    current.source_checked_at = record.source_checked_at
                                observation = existing_observations.get(
                                    (
                                        current.id, record.source_document_sha256,
                                        record.source_url, _fact_sha256(record),
                                    )
                                )
                                if observation is None:
                                    pending_observations.append((current, record, "matching"))
                                elif record.source_checked_at > observation.source_checked_at:
                                    observation.source_checked_at = record.source_checked_at
                        identifier = record.registration_identifier
                        if identifier:
                            total, examples = duplicate_stats_cache[identifier]
                            duplicate_count = total - 1  # 当前来源键必在 incoming 集合中
                            duplicate_examples = tuple(
                                key for key in examples if key != record.source_key
                            )[:5]
                        else:
                            duplicate_count = 0
                            duplicate_examples = ()
                        batch_actions.append(ImportAction(
                            source_namespace=record.source_namespace,
                            source_record_id=record.source_record_id,
                            registration_identifier=identifier,
                            drug_class=record.drug_class,
                            origin=record.origin,
                            regulatory_status=record.regulatory_status,
                            source_document_sha256=record.source_document_sha256,
                            source_document_name=record.source_document_name,
                            action=action,
                            canonical_candidate_id=(
                                candidate_cache.get(identifier) if identifier else None
                            ),
                            duplicate_identifier_count=duplicate_count,
                            duplicate_identifier_examples=duplicate_examples,
                        ))
                    if apply:
                        await session.flush()
                        session.add_all(
                            _observation(record, source.id, kind=kind)
                            for source, record, kind in pending_observations
                        )
                        await session.commit()
            except Exception:
                if apply:
                    await _clean_failed_batch(session_factory, root, created_keys)
                raise
            actions.extend(batch_actions)
            if progress:
                progress(start // batch_size + 1, total_batches, tuple(batch_actions))

    if apply:
        with _storage_lock(root):
            await run_batches()
    else:
        await run_batches()
    return ImportReport(applied=apply, actions=tuple(actions))


async def _run_cli(
    records: tuple[RegulatoryRecord, ...], *, apply: bool, batch_size: int,
    progress: Callable[[int, int, tuple[ImportAction, ...]], None] | None = None,
) -> ImportReport:
    from app.db.session import engine

    factory = async_sessionmaker(engine, expire_on_commit=False)
    return await import_records(
        factory, records, apply=apply, batch_size=batch_size, progress=progress
    )


async def _audit_cli(*, remove: bool) -> tuple[str, ...]:
    from app.db.session import engine

    factory = async_sessionmaker(engine, expire_on_commit=False)
    return await audit_orphans(factory, remove=remove)


def _action_payload(row: ImportAction) -> dict:
    return {
        **row.__dict__,
        "duplicate_identifier_examples": list(row.duplicate_identifier_examples),
        "unmatched": row.unmatched,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="离线 JSON 清单")
    parser.add_argument("--source-root", type=Path, help="本地监管来源原件目录")
    parser.add_argument("--batch-size", type=int, default=100, help="每批 1-200 条")
    parser.add_argument("--apply", action="store_true", help="写入待审暂存表，默认仅预览")
    parser.add_argument("--report", type=Path, help="追加式 JSONL 对账和批次检查点，不覆盖现有文件")
    parser.add_argument("--audit-orphans", action="store_true", help="对账私有来源原件与数据库引用")
    parser.add_argument("--remove-orphans", action="store_true", help="与 --audit-orphans 同用，清理无引用原件")
    args = parser.parse_args(argv)
    if args.remove_orphans and not args.audit_orphans:
        parser.error("--remove-orphans 必须与 --audit-orphans 同用")
    if args.audit_orphans:
        if args.manifest or args.source_root or args.apply or args.report:
            parser.error("孤儿对账不能同时执行清单导入")
        try:
            orphans = asyncio.run(_audit_cli(remove=args.remove_orphans))
            verb = "已清理" if args.remove_orphans else "发现"
            print(f"[regulatory-sources] {verb} {len(orphans)} 个无引用私有来源原件")
            for key in orphans[:20]:
                print(f"  {key}")
            return 0 if args.remove_orphans or not orphans else 2
        except Exception as exc:
            print(f"[regulatory-sources] 孤儿对账失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    if not args.manifest or not args.source_root:
        parser.error("导入时必须同时提供 --manifest 与 --source-root")
    if args.apply and not args.report:
        parser.error("--apply 必须同时提供私有 --report JSONL 检查点路径")

    journal = None
    committed_batches = 0
    try:
        report_path = args.report.expanduser().resolve() if args.report else None
        if report_path:
            public_root = _public_upload_dir()
            if report_path == public_root or report_path.is_relative_to(public_root):
                raise RegulatoryManifestError("对账报告不能写入公开 UPLOAD_DIR")
            if report_path.exists():
                raise FileExistsError(f"对账报告已存在：{report_path}")
        records = load_manifest(args.manifest, args.source_root)
        if not 1 <= args.batch_size <= 200:
            raise ValueError("batch_size 必须在 1 到 200 之间")
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            journal = os.fdopen(descriptor, "w", encoding="utf-8")

        def emit(payload: dict) -> None:
            if journal is None:
                return
            json.dump(payload, journal, ensure_ascii=False)
            journal.write("\n")
            journal.flush()
            os.fsync(journal.fileno())

        emit({
            "event": "started", "manifest_sha256": records[0].manifest_sha256,
            "applied": args.apply, "records": len(records), "batch_size": args.batch_size,
        })

        def progress(number: int, total: int, batch_actions: tuple[ImportAction, ...]) -> None:
            nonlocal committed_batches
            if args.apply:
                committed_batches = number
            verb = "已提交" if args.apply else "已预览"
            print(f"[regulatory-sources] 批次 {number}/{total} {verb}；{len(batch_actions)} 条", flush=True)
            emit({
                "event": "batch_committed" if args.apply else "batch_previewed",
                "batch": number, "total_batches": total,
                "actions": [_action_payload(row) for row in batch_actions],
            })

        report = asyncio.run(_run_cli(
            records, apply=args.apply, batch_size=args.batch_size, progress=progress
        ))
        summary = {
            "records": len(records),
            "insert": report.count("insert"),
            "unchanged": report.count("unchanged"),
            "rechecked": report.count("rechecked"),
            "new_snapshot": report.count("new_snapshot"),
            "conflict": report.count("conflict"),
            "unmatched": report.unmatched_count,
            "duplicate_identifier": report.duplicate_identifier_count,
            "strata": report.strata,
            "strata_by_action": report.strata_by_action,
            "by_source_namespace": report.by_source_namespace,
        }
        emit({"event": "complete", "summary": summary})
        mode = "已暂存" if report.applied else "仅预览"
        print(f"[regulatory-sources] {mode}：{json.dumps(summary, ensure_ascii=False)}")
        print("[regulatory-sources] 来源记录均非说明书；新增行全部 pending，未自动关联或公开。")
        return 2 if summary["conflict"] else 0
    except Exception as exc:
        print(
            f"[regulatory-sources] 失败：{type(exc).__name__}: {exc}；"
            f"此前已提交批次 {committed_batches}，请检查 JSONL 检查点并幂等重跑。",
            file=sys.stderr,
        )
        return 1
    finally:
        if journal is not None:
            journal.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
