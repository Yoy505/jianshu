"""微信小程序 code2session 登录服务。"""

from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.core.config import settings

CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


async def code2session(code: str) -> dict[str, Any]:
    """调用微信 jscode2session 换取 openid/unionid。

    - 微信侧返回 errcode != 0 时抛 502；
    - 联调后门：仅 APP_ENV=dev/test、未配置微信密钥且 code == "dev-code"
      时返回假 openid。
    """
    if code == "dev-code" and settings.APP_ENV not in {"dev", "test"}:
        raise HTTPException(status_code=403, detail="开发登录码不可用于当前环境")

    if not settings.WX_APPID or not settings.WX_SECRET:
        if settings.APP_ENV in {"dev", "test"} and code == "dev-code":
            return {"openid": f"dev_{uuid4().hex[:12]}", "unionid": None}
        raise HTTPException(status_code=502, detail="微信登录未配置，请联系管理员")

    params = {
        "appid": settings.WX_APPID,
        "secret": settings.WX_SECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(CODE2SESSION_URL, params=params)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="微信服务暂时不可用，请稍后重试")

    try:
        data = resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="微信服务返回异常，请稍后重试")

    errcode = data.get("errcode", 0)
    if errcode:
        raise HTTPException(
            status_code=502, detail=f"微信登录失败：{data.get('errmsg', '未知错误')}"
        )
    if not data.get("openid"):
        raise HTTPException(status_code=502, detail="微信登录失败：未返回 openid")
    return data
