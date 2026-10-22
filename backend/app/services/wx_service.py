"""微信小程序 code2session 登录服务。"""

from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException
from redis.exceptions import RedisError

from app.core.config import settings

CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


async def code2session(code: str) -> dict[str, Any]:
    """调用微信 jscode2session 换取 openid/unionid。

    - 微信登录码无效时返回 400，上游异常时返回 502；
    - 联调后门：仅 APP_ENV=dev/test、未配置微信密钥且 code == "dev-code"
      时返回假 openid。
    """
    if code == "dev-code" and settings.APP_ENV not in {"dev", "test"}:
        raise HTTPException(status_code=403, detail="开发登录码不可用于当前环境")

    if not settings.WX_APPID or not settings.WX_SECRET:
        if settings.APP_ENV in {"dev", "test"} and code == "dev-code":
            return {"openid": f"dev_{uuid4().hex[:12]}", "unionid": None}
        raise HTTPException(status_code=503, detail="微信登录未配置，请联系管理员")

    params = {
        "appid": settings.WX_APPID,
        "secret": settings.WX_SECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(CODE2SESSION_URL, params=params)
            resp.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="微信服务暂时不可用，请稍后重试")

    try:
        data = resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="微信服务返回异常，请稍后重试")

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="微信登录返回异常")
    errcode = data.get("errcode", 0)
    if errcode:
        raise HTTPException(
            status_code=400, detail="微信登录凭证已失效，请重新登录"
        )
    if not data.get("openid"):
        raise HTTPException(status_code=502, detail="微信登录失败：未返回 openid")
    return data


async def phone_number(phone_code: str, redis) -> str:
    """消费微信手机号授权码。凭据只保存在后端，手机号不信任客户端输入。"""
    import re

    if not settings.WX_APPID or not settings.WX_SECRET:
        raise HTTPException(status_code=503, detail="微信手机号登录未配置，请联系管理员")
    cache_key = f"wx:stable-access-token:{settings.WX_APPID}"
    try:
        token = await redis.get(cache_key)
        async with httpx.AsyncClient(timeout=10.0) as client:
            if not token:
                response = await client.post(
                    "https://api.weixin.qq.com/cgi-bin/stable_token",
                    json={"grant_type": "client_credential", "appid": settings.WX_APPID,
                          "secret": settings.WX_SECRET, "force_refresh": False},
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("errcode") or not data.get("access_token"):
                    raise HTTPException(status_code=502, detail="微信手机号服务暂不可用，请稍后重试")
                token = data["access_token"]
                await redis.set(cache_key, token, ex=max(1, int(data.get("expires_in", 7200)) - 120))
            response = await client.post(
                "https://api.weixin.qq.com/wxa/business/getuserphonenumber",
                params={"access_token": token}, json={"code": phone_code},
            )
            response.raise_for_status()
            data = response.json()
    except RedisError:
        raise HTTPException(status_code=503, detail="登录服务暂不可用，请稍后重试")
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(status_code=502, detail="微信手机号服务异常，请重新授权后重试")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="微信手机号服务返回异常")
    if data.get("errcode"):
        if data["errcode"] in {40001, 40014, 42001}:
            await redis.delete(cache_key)
        raise HTTPException(status_code=400, detail="手机号授权未完成或已失效，请重新授权")
    info = data.get("phone_info") or {}
    if not isinstance(info, dict) or not isinstance(info.get("watermark"), dict):
        raise HTTPException(status_code=502, detail="微信手机号信息校验失败")
    if info["watermark"].get("appid") != settings.WX_APPID:
        raise HTTPException(status_code=400, detail="手机号授权与当前小程序不匹配")
    # 与现有 H5 手机号账号保持同一规范；境外号码保留区号，避免身份碰撞。
    country = str(info.get("countryCode", ""))
    pure = str(info.get("purePhoneNumber", ""))
    if not re.fullmatch(r"[1-9]\d{0,3}", country) or not re.fullmatch(r"\d{5,15}", pure):
        raise HTTPException(status_code=502, detail="微信手机号信息不完整")
    phone = pure if country == "86" else "+" + country + pure
    if len(phone) > 20 or (country == "86" and not re.fullmatch(r"1[3-9]\d{9}", pure)):
        raise HTTPException(status_code=502, detail="微信手机号格式异常")
    return phone
