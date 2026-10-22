"""设备云数据域业务层（工作二切片 C，2026-10-22）：标准数据域 + 适配接口。

外部边界：设备型号与厂商未书面确定——本层无真实厂商 API 调用。适配器注册表
`_ADAPTERS` 仅在测试/开发环境注入合成适配器（`register_adapter`）；生产为空，
触发同步返回 blocked（厂商适配待配置）并留痕，绝不伪造数据。

质量策略（可测试、留痕、不静默改正）：
- wrong_user（厂商账号标识 ≠ 绑定归属）→ 拒绝入库，同步日志计数；
- invalid_value（数值超出指标合理范围）→ 入库标记，不入趋势/规则；
- late（业务日期距今 > 晚到窗口）→ 入库标记，不入趋势；后台复核后转 reviewed 入趋势；
- 缺失不用 0 填补——趋势如实返回缺口。
趋势与规则只使用 valid/reviewed 且每去重键最大 revision。
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    CONSENT_PURPOSE_STEWARD_DEVICE_DATA,
    DEVICE_METRIC_RANGES,
    TREND_QUALITIES,
    DeviceBinding,
    DeviceBindingStatus,
    DeviceCursorStatus,
    DeviceMetric,
    DeviceQuality,
    DeviceSyncCursor,
    DeviceSyncLog,
    DeviceSyncStatus,
    DeviceVendor,
    MpUserConsent,
    User,
)
from app.schemas.common import as_utc
from app.services import steward_service
from app.services.audit_service import audit

UTC = timezone.utc

NO_ADAPTER_NOTE = "厂商适配待配置（型号与厂商未书面确定，不伪造数据）"


def now_utc() -> datetime:
    return datetime.now(UTC)


def late_days() -> int:
    return max(1, int(getattr(settings, "STEWARD_DEVICE_LATE_DAYS", 7) or 7))


def max_failures() -> int:
    return max(1, int(getattr(settings, "STEWARD_DEVICE_MAX_SYNC_FAILURES", 5) or 5))


# ───────────────────────── 厂商适配接口 ─────────────────────────


class DeviceVendorAdapter(Protocol):
    """厂商适配接口：真实厂商确定后按此实现并注册；当前无实现。"""

    async def pull(
        self, binding: DeviceBinding, cursor: str, user_key: str
    ) -> tuple[list[dict[str, Any]], str]:
        """返回 (records, next_cursor)。record 字段：
        {vendor_user_key, vendor_record_id, vendor_updated_at, metrics: [
          {metric_type, value, business_date, tz_offset_minutes}]}
        next_cursor 为空串表示无可推进。"""


_ADAPTERS: dict[str, DeviceVendorAdapter] = {}


def register_adapter(vendor_code: str, adapter: DeviceVendorAdapter) -> None:
    """注册适配器。仅测试/开发注入合成适配器；真实厂商接入另开切片（凭据/回执另验）。"""
    _ADAPTERS[vendor_code] = adapter


def clear_adapters() -> None:
    _ADAPTERS.clear()


# ───────────────────────── 厂商与绑定 ─────────────────────────


async def create_vendor(db: AsyncSession, admin, code: str, name: str, note: str | None) -> DeviceVendor:
    duplicate = (
        await db.execute(select(DeviceVendor).where(DeviceVendor.code == code))
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该厂商代码已存在")
    vendor = DeviceVendor(code=code, name=name, note=note, created_by_admin_id=admin.id)
    db.add(vendor)
    await audit(
        db, action="device.vendor.create", resource_type="device_vendor", resource_id=vendor.id,
        detail={"code": code, "name": name}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(vendor)
    return vendor


async def create_binding(
    db: AsyncSession, admin, user_id: int, vendor_id: int, device_model: str, device_sn: str
) -> DeviceBinding:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    vendor = await db.get(DeviceVendor, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="厂商不存在")
    # 跨用户归属校验：同一设备已有 active 绑定（不论归属谁）→ 拒绝
    conflict = (
        await db.execute(
            select(DeviceBinding).where(
                DeviceBinding.vendor_id == vendor_id,
                DeviceBinding.device_sn == device_sn,
                DeviceBinding.status == DeviceBindingStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if conflict is not None:
        raise HTTPException(status_code=409, detail="该设备已有有效绑定，不能重复绑定（跨用户归属校验）")
    binding = DeviceBinding(
        user_id=user_id, vendor_id=vendor_id, device_model=device_model, device_sn=device_sn,
        created_by_admin_id=admin.id,
    )
    db.add(binding)
    await db.flush()  # 取 binding.id 供游标行外键
    db.add(DeviceSyncCursor(binding_id=binding.id))
    await audit(
        db, action="device.binding.create", resource_type="device_binding", resource_id=binding.id,
        detail={"user_id": user_id, "vendor_id": vendor_id, "device_sn": device_sn},
        actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(binding)
    return binding


async def unbind(db: AsyncSession, actor, binding: DeviceBinding, reason: str, *, by_user: bool = False) -> DeviceBinding:
    if binding.status != DeviceBindingStatus.ACTIVE.value:
        raise HTTPException(status_code=409, detail="该绑定已解绑")
    result = await db.execute(
        update(DeviceBinding)
        .where(
            DeviceBinding.id == binding.id,
            DeviceBinding.status == DeviceBindingStatus.ACTIVE.value,
        )
        .values(
            status=DeviceBindingStatus.UNBOUND.value,
            unbound_at=now_utc(),
            unbind_reason=reason,
            active_flag=binding.id,  # 解除 active 唯一占位，允许换绑新行
        )
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="解绑状态冲突，请刷新后重试")
    await audit(
        db, action="device.binding.unbind", resource_type="device_binding", resource_id=binding.id,
        detail={"reason": reason, "by_user": by_user},
        actor_type="user" if by_user else "admin",
        actor_id=actor.id if by_user else actor.id,
        actor_name=steward_service.user_actor_name(actor) if by_user else actor.name,
    )
    await db.commit()
    await db.refresh(binding)
    return binding


async def active_binding_for_user(db: AsyncSession, user_id: int) -> DeviceBinding | None:
    return (
        await db.execute(
            select(DeviceBinding)
            .where(
                DeviceBinding.user_id == user_id,
                DeviceBinding.status == DeviceBindingStatus.ACTIVE.value,
            )
            .order_by(DeviceBinding.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def has_valid_data(db: AsyncSession, user_id: int) -> bool:
    """first_valid_data 门禁：active 绑定下存在 ≥1 条 valid/reviewed 数据。"""
    binding = await active_binding_for_user(db, user_id)
    if binding is None:
        return False
    exists = (
        await db.execute(
            select(DeviceMetric.id)
            .where(
                DeviceMetric.binding_id == binding.id,
                DeviceMetric.quality.in_(TREND_QUALITIES),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return exists is not None


# ───────────────────────── 同步 ─────────────────────────


async def _device_consent_ok(db: AsyncSession, user_id: int) -> bool:
    from app.services.steward_service import current_terms, _matched_consent

    terms = await current_terms(db, CONSENT_PURPOSE_STEWARD_DEVICE_DATA)
    if terms is None:
        return False
    matched = await _matched_consent(db, user_id, CONSENT_PURPOSE_STEWARD_DEVICE_DATA, terms.version)
    return matched is not None


def _classify(value: float, metric_type: str, business_date: date) -> str:
    """质量判定：范围 → invalid_value；晚到 → late；否则 valid。"""
    lo, hi, _unit = DEVICE_METRIC_RANGES.get(metric_type, (float("-inf"), float("inf"), ""))
    if not (lo <= float(value) <= hi):
        return DeviceQuality.INVALID_VALUE.value
    if business_date < date.today() - timedelta(days=late_days()):
        return DeviceQuality.LATE.value
    return DeviceQuality.VALID.value


async def sync_binding(db: AsyncSession, binding_id: int, *, trigger: str = "manual") -> dict:
    """同步一个绑定：前置检查 → 适配器拉取 → 分类入库（去重/修订）→ 游标与日志。

    幂等：同游标重跑仅产生重复跳过；异常不推进游标；连续失败达上限 blocked。
    """
    binding = await db.get(DeviceBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail="绑定不存在")
    started = now_utc()
    cursor = (
        await db.execute(select(DeviceSyncCursor).where(DeviceSyncCursor.binding_id == binding.id))
    ).scalar_one_or_none()
    if cursor is None:
        cursor = DeviceSyncCursor(binding_id=binding.id)
        db.add(cursor)
        await db.flush()

    log = DeviceSyncLog(binding_id=binding.id, started_at=started, status=DeviceSyncStatus.BLOCKED.value)
    db.add(log)

    async def _finish(status: str, detail: str, counts: dict | None = None, *, advance: str | None = None, fail: bool = False) -> dict:
        log.status = status
        log.detail = detail[:500]
        log.finished_at = now_utc()
        for field in ("pulled", "stored", "duplicates", "revisions", "invalid", "late", "wrong_user"):
            setattr(log, field, int((counts or {}).get(field, 0)))
        cursor.last_attempt_at = now_utc()
        if advance is not None and status in (DeviceSyncStatus.SUCCESS.value, DeviceSyncStatus.PARTIAL.value):
            cursor.cursor = advance
            cursor.last_success_at = now_utc()
            cursor.failure_count = 0
            cursor.status = DeviceCursorStatus.IDLE.value
        elif fail:
            cursor.failure_count = (cursor.failure_count or 0) + 1
            cursor.last_error = detail[:500]
            if cursor.failure_count >= max_failures():
                cursor.status = DeviceCursorStatus.BLOCKED.value
        await audit(
            db, action="device.sync.run", resource_type="device_binding", resource_id=binding.id,
            detail={"status": status, "trigger": trigger, **(counts or {})},
            actor_type="system",
        )
        await db.commit()
        return {"status": status, "detail": detail, **(counts or {})}

    # 前置检查（执行前重查）
    if binding.status != DeviceBindingStatus.ACTIVE.value:
        return await _finish("blocked", "绑定已解绑，停止同步")
    vendor = await db.get(DeviceVendor, binding.vendor_id)
    if vendor is None or vendor.status != "active":
        return await _finish("blocked", "厂商不可用，停止同步")
    if not await _device_consent_ok(db, binding.user_id):
        return await _finish("blocked", "设备厂商数据授权缺失或已撤回，停止同步")
    if cursor.status == DeviceCursorStatus.BLOCKED.value:
        return await _finish("blocked", "连续失败达上限已封锁，须人工重置游标")
    adapter = _ADAPTERS.get(vendor.code)
    if adapter is None:
        return await _finish("blocked", NO_ADAPTER_NOTE)

    user = await db.get(User, binding.user_id)
    user_key = user.wx_openid or (user.phone or f"user-{user.id}")
    try:
        records, next_cursor = await adapter.pull(binding, cursor.cursor or "", user_key)
    except Exception as exc:  # noqa: BLE001 —— 适配器异常统一转为失败记录，不中断批处理
        return await _finish("failed", f"适配器异常：{type(exc).__name__}: {exc}", fail=True)

    counts = {"pulled": 0, "stored": 0, "duplicates": 0, "revisions": 0, "invalid": 0, "late": 0, "wrong_user": 0}
    for record in records:
        counts["pulled"] += 1
        # 错人：拒绝入库（不落他人健康数据），仅计数留痕
        if str(record.get("vendor_user_key", "")) != user_key:
            counts["wrong_user"] += 1
            continue
        try:
            raw_updated = record["vendor_updated_at"]
            vendor_updated_at = as_utc(
                raw_updated if isinstance(raw_updated, datetime) else datetime.fromisoformat(str(raw_updated))
            )
        except (KeyError, TypeError, ValueError):
            counts["invalid"] += 1
            continue
        for metric in record.get("metrics", []):
            metric_type = str(metric.get("metric_type", ""))
            if metric_type not in DEVICE_METRIC_RANGES:
                counts["invalid"] += 1
                continue
            try:
                value = float(metric["value"])
                business_date = date.fromisoformat(str(metric["business_date"]))
            except (KeyError, TypeError, ValueError):
                counts["invalid"] += 1
                continue
            dedup_key = f"{record['vendor_record_id']}:{metric_type}"
            existing = (
                await db.execute(
                    select(DeviceMetric)
                    .where(
                        DeviceMetric.binding_id == binding.id,
                        DeviceMetric.dedup_key == dedup_key,
                    )
                    .order_by(DeviceMetric.revision.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if as_utc(existing.vendor_updated_at) >= vendor_updated_at:
                    counts["duplicates"] += 1  # 重复拉取：跳过
                    continue
                revision = existing.revision + 1  # 合法修订：追加新版本
                counts["revisions"] += 1
            else:
                revision = 1
            quality = _classify(value, metric_type, business_date)
            if quality == DeviceQuality.INVALID_VALUE.value:
                counts["invalid"] += 1
            elif quality == DeviceQuality.LATE.value:
                counts["late"] += 1
            row = DeviceMetric(
                binding_id=binding.id,
                user_id=binding.user_id,
                metric_type=metric_type,
                value=value,
                unit=DEVICE_METRIC_RANGES[metric_type][2],
                business_date=business_date,
                tz_offset_minutes=int(metric.get("tz_offset_minutes", 480)),
                dedup_key=dedup_key,
                vendor_record_id=str(record["vendor_record_id"]),
                vendor_updated_at=vendor_updated_at,
                quality=quality,
                revision=revision,
            )
            db.add(row)
            counts["stored"] += 1
    status = DeviceSyncStatus.SUCCESS.value if not counts["invalid"] else DeviceSyncStatus.PARTIAL.value
    return await _finish(
        status,
        f"拉取 {counts['pulled']} 条记录；入库 {counts['stored']}（重复 {counts['duplicates']}、修订 {counts['revisions']}、无效 {counts['invalid']}、晚到 {counts['late']}、错人 {counts['wrong_user']}）",
        counts,
        advance=(next_cursor or cursor.cursor or ""),
    )


async def reset_cursor(db: AsyncSession, admin, binding: DeviceBinding, reason: str, *, back: bool = False) -> DeviceSyncCursor:
    cursor = (
        await db.execute(select(DeviceSyncCursor).where(DeviceSyncCursor.binding_id == binding.id))
    ).scalar_one()
    cursor.status = DeviceCursorStatus.IDLE.value
    cursor.failure_count = 0
    cursor.last_error = None
    if back:
        cursor.cursor = ""  # 游标回退：重拉补传窗口
    await audit(
        db, action="device.cursor.reset", resource_type="device_binding", resource_id=binding.id,
        detail={"reason": reason, "back": back}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(cursor)
    return cursor


async def review_metric(db: AsyncSession, admin, metric: DeviceMetric, reason: str) -> DeviceMetric:
    """晚到复核：late → reviewed（解除排除并留痕）；不改正数值与质量历史。"""
    if metric.quality != DeviceQuality.LATE.value:
        raise HTTPException(status_code=409, detail="仅晚到数据可复核解除")
    metric.quality = DeviceQuality.REVIEWED.value
    metric.review_note = reason
    await audit(
        db, action="device.metric.review", resource_type="device_metric", resource_id=metric.id,
        detail={"reason": reason}, actor_id=admin.id, actor_name=admin.name,
    )
    await db.commit()
    await db.refresh(metric)
    return metric


# ───────────────────────── 趋势（C 端） ─────────────────────────


async def user_trend(db: AsyncSession, user_id: int, metric: str, days: int) -> dict:
    """趋势：仅本人 active 绑定下 valid/reviewed 且每去重键最大 revision，按业务日聚合。
    无数据日不填补（gaps 如实返回）。"""
    binding = await active_binding_for_user(db, user_id)
    if binding is None:
        return {"dates": [], "series": [], "gaps": [], "last_synced_at": None, "quality_note": "尚未绑定设备"}
    cursor = (
        await db.execute(select(DeviceSyncCursor).where(DeviceSyncCursor.binding_id == binding.id))
    ).scalar_one_or_none()
    # 每键最大 revision
    latest_ids = (
        select(func.max(DeviceMetric.id).label("mid"))
        .where(
            DeviceMetric.binding_id == binding.id,
            DeviceMetric.metric_type == metric,
            DeviceMetric.quality.in_(TREND_QUALITIES),
            DeviceMetric.business_date >= date.today() - timedelta(days=days),
        )
        .group_by(DeviceMetric.dedup_key)
        .subquery()
    )
    rows = (
        await db.execute(
            select(DeviceMetric.business_date, DeviceMetric.value)
            .join(latest_ids, DeviceMetric.id == latest_ids.c.mid)
            .order_by(DeviceMetric.business_date.asc())
        )
    ).all()
    by_date: dict[date, float] = {}
    for day, value in rows:
        by_date[day] = float(value)  # 同日多记录取最后一条（简明聚合口径）
    dates = sorted(by_date)
    gaps = []
    day_iter = dates[0] if dates else None
    if day_iter is not None:
        cursor_date = day_iter
        while cursor_date <= dates[-1]:
            if cursor_date not in by_date:
                gaps.append(cursor_date.isoformat())
            cursor_date += timedelta(days=1)
    quality_note = "数据仅用于健康记录展示，不作为医学诊断依据"
    return {
        "dates": [d.isoformat() for d in dates],
        "series": [by_date[d] for d in dates],
        "gaps": gaps,
        "last_synced_at": cursor.last_success_at if cursor else None,
        "quality_note": quality_note,
    }


async def sync_all_active(db: AsyncSession) -> dict:
    """批量同步全部 active 绑定（beat 周期；单绑定异常不中断批处理）。"""
    bindings = (
        await db.execute(
            select(DeviceBinding.id).where(DeviceBinding.status == DeviceBindingStatus.ACTIVE.value)
        )
    ).scalars().all()
    result = {"total": len(bindings), "success": 0, "partial": 0, "failed": 0, "blocked": 0}
    for binding_id in bindings:
        try:
            outcome = await sync_binding(db, binding_id, trigger="beat")
            result[outcome["status"]] = result.get(outcome["status"], 0) + 1
        except HTTPException:
            result["failed"] += 1
    return result
