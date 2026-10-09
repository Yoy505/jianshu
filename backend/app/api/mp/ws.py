"""小程序 · 咚咚实时通道（WebSocket）：鉴权、收发消息、自动应答推送（契约 api.md 五之二）。

鉴权用 query 参数 token（wx.connectSocket 对自定义 header 的兼容性差）；
无效 token 以 4401 关闭。业务错误发 error 帧不断连。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db, get_redis
from app.core.security import SCOPE_USER, TOKEN_TYPE_ACCESS, decode_token
from app.models import MpProvider, MpService, User, UserStatus
from app.services.consult_service import (
    append_user_message_and_reply,
    load_own_open_consult,
    message_payload,
    validate_content,
)
from app.services.rate_limit import check_user_rate_limit
from app.services.ws_manager import manager

logger = logging.getLogger("app.ws")

router = APIRouter(tags=["mp-ws"])

WS_CLOSE_UNAUTHORIZED = 4401


@router.websocket("/ws")
async def mp_ws(
    websocket: WebSocket,
    token: str = "",
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    # ---- 鉴权：user scope access token ----
    user: User | None = None
    try:
        payload = decode_token(token)
        if payload.get("scope") == SCOPE_USER and payload.get("type") == TOKEN_TYPE_ACCESS:
            user = (
                await db.execute(
                    select(User).where(
                        User.id == int(payload.get("sub", "")),
                        User.status == UserStatus.ACTIVE.value,
                    )
                )
            ).scalar_one_or_none()
    except Exception:
        user = None
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    user_id = user.id
    await websocket.accept()
    manager.register(user_id, websocket)

    try:
        await websocket.send_json({"type": "connected", "user_id": user_id})
        while True:
            frame = await websocket.receive_json()

            # 连接建立后令牌可能过期、用户也可能被封禁；每帧重新校验。
            await db.rollback()
            try:
                current_token = decode_token(token)
                token_valid = (
                    current_token.get("scope") == SCOPE_USER
                    and current_token.get("type") == TOKEN_TYPE_ACCESS
                    and int(current_token.get("sub", "")) == user_id
                )
            except Exception:
                token_valid = False
            active = await db.scalar(
                select(User.id).where(User.id == user_id, User.status == UserStatus.ACTIVE.value)
            )
            if not token_valid or active is None:
                await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
                return

            if not isinstance(frame, dict):
                await websocket.send_json({"type": "error", "detail": "消息帧格式不正确"})
                continue

            if frame.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if frame.get("type") != "message":
                await websocket.send_json({"type": "error", "detail": "未知的消息类型"})
                continue

            try:
                consult_id = int(frame.get("consult_id", ""))
            except (TypeError, ValueError):
                await websocket.send_json({"type": "error", "detail": "consult_id 无效"})
                continue

            try:
                if settings.APP_ENV not in {"dev", "test"}:
                    raise HTTPException(
                        status_code=503,
                        detail="在线咨询暂未开放：健康信息授权与真人服务流程尚未接入",
                    )
                # 与 REST 同一限流口径（20 次/分钟）
                await check_user_rate_limit(
                    redis, user_id=user_id, action="mp.messages", limit=20
                )
                content = validate_content(frame.get("content"))
                consult = await load_own_open_consult(db, user_id, consult_id)
                service = await db.get(MpService, consult.service_id)
                provider = await db.get(MpProvider, consult.provider_id)
                user_message, reply_message = await append_user_message_and_reply(
                    db, consult, content, service, provider
                )
                await db.commit()
                # 先回显（确认落库）再推回复；多端在线全部收到
                await manager.send_to_user(
                    user_id,
                    {"type": "message", "consult_id": consult.id, "message": message_payload(user_message)},
                )
                await manager.send_to_user(
                    user_id,
                    {"type": "message", "consult_id": consult.id, "message": message_payload(reply_message)},
                )
            except Exception as exc:
                await db.rollback()
                # 业务校验 / 限流（HTTPException）与意外错误：error 帧，不断连
                detail = getattr(exc, "detail", "消息发送失败，请稍后再试")
                await websocket.send_json({"type": "error", "detail": str(detail)})
    except WebSocketDisconnect:
        pass
    finally:
        manager.unregister(user_id, websocket)
        await db.close()
