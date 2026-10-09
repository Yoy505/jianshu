"""咚咚咨询共享业务：内容校验、消息写入与系统自动回执（REST 与 WebSocket 复用）。

真人专业人员接入后，应由独立的实名发送流程写入 provider 消息；
演示回执始终使用 system 身份。
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    MpConsult,
    MpConsultMessage,
    MpConsultStatus,
    MpProvider,
    MpProviderService,
    MpService,
)

CONTENT_MAX_LENGTH = 2000


def build_system_receipt() -> str:
    """演示环境的固定回执；不冒充专业人员，也不处理个人健康问题。"""
    return (
        "系统自动回执：已收到你的消息。当前咨询为演示服务，尚未接入真人专业人员；"
        "本回执不提供诊断、治疗或个体用药建议，也不代表医师或药师答复。"
        "如有紧急不适，请立即拨打 120 或前往就近急诊。"
    )


async def validate_consult_target(
    db: AsyncSession, service_id: int, provider_id: int
) -> tuple[MpService, MpProvider]:
    service = await db.get(MpService, service_id)
    if service is None or not service.on_shelf:
        raise HTTPException(status_code=404, detail="服务不存在或已下架")
    provider = await db.get(MpProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    linked = await db.scalar(
        select(func.count())
        .select_from(MpProviderService)
        .where(
            MpProviderService.service_id == service.id,
            MpProviderService.provider_id == provider.id,
        )
    )
    if int(linked or 0) == 0:
        raise HTTPException(status_code=400, detail="该人员暂不提供此项服务")
    return service, provider


def validate_content(content: str | None) -> str:
    content = (content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息内容不能为空")
    if len(content) > CONTENT_MAX_LENGTH:
        raise HTTPException(status_code=400, detail=f"消息内容不能超过 {CONTENT_MAX_LENGTH} 字")
    return content


def message_payload(message: MpConsultMessage, consult_id: int | None = None) -> dict:
    """WS 推送与 REST 共用的消息 JSON 结构。"""
    created_at = message.created_at
    if created_at.tzinfo is None:
        from datetime import timezone as tz

        created_at = created_at.replace(tzinfo=tz.utc)
    return {
        "id": message.id,
        "consult_id": consult_id if consult_id is not None else message.consult_id,
        "sender": message.sender,
        "content": message.content,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
    }


async def append_user_message_and_reply(
    db: AsyncSession, consult: MpConsult, content: str, service: MpService, provider: MpProvider
) -> tuple[MpConsultMessage, MpConsultMessage]:
    """写入用户消息与系统自动回执，同时推进会话活跃时间。"""
    now = datetime.now(timezone.utc)
    user_message = MpConsultMessage(
        consult_id=consult.id, sender="user", content=content, created_at=now
    )
    db.add(user_message)
    await db.flush()

    reply_message = MpConsultMessage(
        consult_id=consult.id,
        sender="system",
        content=build_system_receipt(),
        created_at=now,
    )
    db.add(reply_message)
    consult.last_message_at = now
    await db.flush()
    return user_message, reply_message


async def load_own_open_consult(db: AsyncSession, user_id: int, consult_id: int) -> MpConsult:
    """加载本人会话并校验仍在进行中（REST 与 WS 共用）。"""
    consult = await db.get(MpConsult, consult_id)
    if consult is None or consult.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if consult.status != MpConsultStatus.OPEN.value:
        raise HTTPException(status_code=409, detail="会话已结束，无法继续发送消息")
    return consult
