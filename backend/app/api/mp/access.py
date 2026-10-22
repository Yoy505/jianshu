"""匿名用户仅可读取模块首页目录；详情、检索、分页与业务操作需登录。"""
from fastapi import Depends
from starlette.requests import HTTPConnection
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db


async def require_module_access(connection: HTTPConnection, db: AsyncSession = Depends(get_db)) -> None:
    # WebSocket 使用自身握手 token 校验，不能以 HTTP Header 规则替代。
    if connection.scope["type"] != "http":
        return
    path = connection.url.path.rstrip("/")
    public = {
        "/api/v1/mp/home", "/api/v1/mp/scenarios", "/api/v1/mp/services",
        "/api/v1/mp/drugs", "/api/v1/mp/products", "/api/v1/mp/packages",
    }
    query = connection.query_params
    uses_search = any(query.get(key) for key in ("q", "tag", "letter", "category", "service_type"))
    uses_more = query.get("page", "1") != "1"
    if connection.scope["method"] == "GET" and path in public and not uses_search and not uses_more:
        return
    await get_current_user(authorization=connection.headers.get("authorization"), db=db)
