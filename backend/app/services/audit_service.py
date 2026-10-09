"""审计写入服务：与业务同一事务提交，保证「操作成功必有留痕」。"""

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def client_ip(request: Request | None) -> str | None:
    """取真实客户端 IP（反代场景读 X-Forwarded-For 首段，兼容 X-Real-IP）。"""
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else None)


async def audit(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    detail: dict[str, Any] | None = None,
    actor_type: str = "admin",
    actor_id: int | None = None,
    actor_name: str = "",
    request: Request | None = None,
) -> AuditLog:
    """写入一条审计记录（随当前事务 commit 生效，业务回滚则一并丢弃）。"""
    entry = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        ip_address=client_ip(request),
    )
    db.add(entry)
    return entry
