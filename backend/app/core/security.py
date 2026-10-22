"""密码哈希（pwdlib/argon2）与 JWT（PyJWT, HS256）。"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()  # 默认 argon2id

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
SCOPE_ADMIN = "admin"
SCOPE_USER = "user"
# 采购单位查看账号（工作一）：只读本单位批次汇总，与 admin/user 互不通用
SCOPE_ORG = "org"


def hash_password(raw: str) -> str:
    return password_hash.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return password_hash.verify(raw, hashed)


def create_token(sub: str | int, scope: str, type_: str) -> str:
    """签发 JWT；sub 为用户/账号 id，scope 区分 admin|user，type 区分 access|refresh。"""
    now = datetime.now(timezone.utc)
    if type_ == TOKEN_TYPE_ACCESS:
        expires = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES)
    else:
        expires = now + timedelta(days=settings.JWT_REFRESH_TOKEN_DAYS)
    payload: dict[str, Any] = {
        "sub": str(sub),
        "scope": scope,
        "type": type_,
        "iat": now,
        "exp": expires,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    """校验并解码 JWT，任何失败统一抛 401。"""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的登录凭证")
