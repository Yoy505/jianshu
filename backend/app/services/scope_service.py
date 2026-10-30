"""后台健康域访问范围（F02，2026-10-26）。

健康正文（服务实例、任务、触达、设备指标、联系人、专业工单、报告）的可见范围由服务端
统一计算，前端隐藏按钮只是体验优化。规则：

- 普通岗位（负责管家 / 替补管家 / 医师）：后台账号通过 steward_staff_admin_links 绑定到
  在职人员；可见实例 = 当前有效指派关系指向这些人员的实例——实例的 butler / backup_butler /
  physician、未结专业工单的指派医师、未终态触达意图的受理人。计划和任务仅记录历史责任，
  不独立授予访问权。指派改变、交接、撤销绑定或人员停用后，下一次请求即按新关系计算。
- 特权管理（权限点 steward.scope.all，当前仅 admin 角色）：可跨范围访问；读取不在本人
  范围内的实例详情时写审计（steward.scope.cross_read，附访问理由），便于追溯。
- 财务 / 单位 / 无绑定的编辑：没有任何可见实例，列表为空、详情 403，不以筛选参数、资源 ID
  或导出绕过。

列表、总数、详情、操作、后台任务共用本模块的谓词与校验函数，不在各路由重复实现。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import false, or_, select, true, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Select

from app.core.permissions import STEWARD_SCOPE_ALL, role_has
from app.models import (
    OPEN_ORDER_STATUSES,
    OutreachIntent,
    OutreachIntentStatus,
    StewardProfessionalOrder,
    StewardServiceInstance,
    StewardStaff,
    StewardStaffAdminLink,
)
from app.services.audit_service import audit

CROSS_SCOPE_READ_ACTION = "steward.scope.cross_read"

_OPEN_INTENT_STATUSES = (
    OutreachIntentStatus.PENDING_CHANNEL.value,
    OutreachIntentStatus.PENDING_MANUAL.value,
    OutreachIntentStatus.PENDING_CONFIRM.value,
)


@dataclass(frozen=True)
class AdminScope:
    admin_id: int
    role: str
    privileged: bool
    staff_ids: frozenset[int]

    @property
    def unrestricted(self) -> bool:
        return self.privileged

    @property
    def empty(self) -> bool:
        return not self.privileged and not self.staff_ids


async def load_scope(db: AsyncSession, admin) -> AdminScope:
    """当前账号的范围：特权标志 + 绑定的在职人员 ID（停用人员的绑定不计入）。"""
    rows = (
        await db.execute(
            select(StewardStaffAdminLink.staff_id)
            .join(StewardStaff, StewardStaff.id == StewardStaffAdminLink.staff_id)
            .where(StewardStaffAdminLink.admin_id == admin.id, StewardStaff.status == "active")
        )
    ).scalars().all()
    return AdminScope(
        admin_id=admin.id,
        role=admin.role,
        privileged=role_has(admin.role, STEWARD_SCOPE_ALL),
        staff_ids=frozenset(int(r) for r in rows),
    )


def visible_instance_ids(scope: AdminScope) -> Select:
    """非特权账号可见实例 ID 的子查询（各类有效指派关系的并集）。"""
    staff = list(scope.staff_ids)
    by_instance = select(StewardServiceInstance.id).where(
        or_(
            StewardServiceInstance.butler_id.in_(staff),
            StewardServiceInstance.backup_butler_id.in_(staff),
            StewardServiceInstance.physician_id.in_(staff),
        )
    )
    by_order = select(StewardProfessionalOrder.instance_id).where(
        StewardProfessionalOrder.physician_id.in_(staff),
        StewardProfessionalOrder.status.in_(OPEN_ORDER_STATUSES),
    )
    by_intent = select(OutreachIntent.instance_id).where(
        OutreachIntent.assignee_staff_id.in_(staff),
        OutreachIntent.status.in_(_OPEN_INTENT_STATUSES),
    )
    return union(by_instance, by_order, by_intent)


def instance_predicate(scope: AdminScope, column: ColumnElement = StewardServiceInstance.id) -> ColumnElement:
    """把范围套到任意带实例 ID 列的查询上（列表与总数必须使用同一谓词）。"""
    if scope.privileged:
        return true()
    if not scope.staff_ids:
        return false()
    return column.in_(visible_instance_ids(scope))


def user_predicate(scope: AdminScope, column: ColumnElement) -> ColumnElement:
    """用户级资料（设备绑定 / 指标 / 联系人 / 签发计划）：用户须至少有一个可见实例。"""
    if scope.privileged:
        return true()
    if not scope.staff_ids:
        return false()
    visible_users = select(StewardServiceInstance.user_id).where(
        StewardServiceInstance.id.in_(visible_instance_ids(scope))
    )
    return column.in_(visible_users)


async def instance_in_scope(db: AsyncSession, scope: AdminScope, instance_id: int) -> bool:
    if scope.privileged:
        return True
    if not scope.staff_ids:
        return False
    hit = (
        await db.execute(
            select(StewardServiceInstance.id).where(
                StewardServiceInstance.id == instance_id, instance_predicate(scope)
            )
        )
    ).scalar_one_or_none()
    return hit is not None


async def user_in_scope(db: AsyncSession, scope: AdminScope, user_id: int) -> bool:
    if scope.privileged:
        return True
    if not scope.staff_ids:
        return False
    hit = (
        await db.execute(
            select(StewardServiceInstance.id)
            .where(StewardServiceInstance.user_id == user_id, instance_predicate(scope))
            .limit(1)
        )
    ).scalar_one_or_none()
    return hit is not None


async def _own_scope_hit(db: AsyncSession, scope: AdminScope, instance_id: int) -> bool:
    """特权账号本人是否也在指派范围内（决定是否记跨范围审计）。"""
    if not scope.staff_ids:
        return False
    own = AdminScope(scope.admin_id, scope.role, False, scope.staff_ids)
    return await instance_in_scope(db, own, instance_id)


async def require_instance_access(
    db: AsyncSession,
    admin,
    instance_id: int,
    *,
    audit_cross_scope: bool = True,
    request: Request | None = None,
    reason: str | None = None,
) -> StewardServiceInstance:
    """实例级入口：404 → 范围校验（403）→ 特权跨范围读取写审计。返回已做惰性状态推进的实例。"""
    from app.services import steward_service

    instance = await steward_service.instance_for_admin(db, instance_id)
    scope = await load_scope(db, admin)
    if not scope.privileged:
        if await instance_in_scope(db, scope, instance.id):
            return instance
        raise HTTPException(status_code=403, detail="该服务实例不在你的指派范围内")
    if audit_cross_scope and not await _own_scope_hit(db, scope, instance.id):
        await audit(
            db,
            action=CROSS_SCOPE_READ_ACTION,
            resource_type="steward_service_instance",
            resource_id=instance.id,
            detail={"user_id": instance.user_id, "reason": (reason or "")[:200] or None},
            actor_id=admin.id,
            actor_name=admin.name,
            request=request,
        )
        await db.commit()
    return instance


async def require_user_access(
    db: AsyncSession,
    admin,
    user_id: int,
    *,
    audit_cross_scope: bool = True,
    request: Request | None = None,
    reason: str | None = None,
) -> None:
    """用户级入口（设备 / 联系人 / 签发计划）：范围外 403；特权跨范围写审计。"""
    scope = await load_scope(db, admin)
    if not scope.privileged:
        if await user_in_scope(db, scope, user_id):
            return
        raise HTTPException(status_code=403, detail="该用户资料不在你的指派范围内")
    if not audit_cross_scope:
        return
    own = AdminScope(scope.admin_id, scope.role, False, scope.staff_ids)
    if not (own.staff_ids and await user_in_scope(db, own, user_id)):
        await audit(
            db,
            action=CROSS_SCOPE_READ_ACTION,
            resource_type="user",
            resource_id=user_id,
            detail={"reason": (reason or "")[:200] or None},
            actor_id=admin.id,
            actor_name=admin.name,
            request=request,
        )
        await db.commit()
