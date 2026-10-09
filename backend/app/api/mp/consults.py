"""小程序 · 咚咚在线咨询：会话创建/复用、增量消息轮询、发送（同步自动应答）。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_redis, pagination_params
from app.models import (
    MpConsult,
    MpConsultMessage,
    MpConsultStatus,
    MpProvider,
    MpProviderService,
    MpService,
    User,
)
from app.schemas.common import Page, make_excerpt
from app.schemas.mp import (
    ConsultCreate,
    ConsultMessageCreate,
    ConsultMessageOut,
    ConsultOut,
    ConsultProviderRef,
    ConsultServiceRef,
)
from redis.asyncio import Redis

from app.core.config import settings
from app.services.consult_service import (
    append_user_message_and_reply,
    validate_consult_target,
    validate_content,
)
from app.services.rate_limit import check_user_rate_limit

router = APIRouter()


def _require_consult_demo_open() -> None:
    """正式环境未建立健康对话同意与真人服务流程前，拒绝收集新消息。"""
    if settings.APP_ENV in ("staging", "prod"):
        raise HTTPException(
            status_code=503,
            detail="在线咨询暂未开放：健康信息授权与真人服务流程尚未接入",
        )


@router.post("/consults", status_code=201)
async def create_consult(
    body: ConsultCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _require_consult_demo_open()
    service, provider = await validate_consult_target(db, body.service_id, body.provider_id)
    content = validate_content(body.content)

    # 同一 user + service + provider 已有 open 会话则复用（新内容照常写入）
    consult = (
        await db.execute(
            select(MpConsult).where(
                MpConsult.user_id == user.id,
                MpConsult.service_id == service.id,
                MpConsult.provider_id == provider.id,
                MpConsult.status == MpConsultStatus.OPEN.value,
            )
        )
    ).scalar_one_or_none()

    reused = consult is not None
    if consult is None:
        consult = MpConsult(
            user_id=user.id,
            service_id=service.id,
            provider_id=provider.id,
            status=MpConsultStatus.OPEN.value,
        )
        db.add(consult)
        await db.flush()

    await append_user_message_and_reply(db, consult, content, service, provider)
    await db.commit()
    return {"id": consult.id, "reused": reused}


@router.get("/consults", response_model=Page[ConsultOut])
async def list_consults(
    pagination: tuple[int, int] = Depends(pagination_params),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[ConsultOut]:
    page, page_size = pagination
    filters = [MpConsult.user_id == user.id]
    total = await db.scalar(select(func.count()).select_from(MpConsult).where(*filters))
    rows = (
        await db.execute(
            select(MpConsult, MpService, MpProvider)
            .join(MpService, MpConsult.service_id == MpService.id)
            .join(MpProvider, MpConsult.provider_id == MpProvider.id)
            .where(*filters)
            .order_by(MpConsult.last_message_at.desc(), MpConsult.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    consults = [consult for consult, _, _ in rows]
    last_messages: dict[int, MpConsultMessage] = {}
    if consults:
        message_rows = (
            await db.execute(
                select(MpConsultMessage)
                .where(MpConsultMessage.consult_id.in_([c.id for c in consults]))
                .order_by(MpConsultMessage.id.asc())
            )
        ).scalars().all()
        for message in message_rows:
            last_messages[message.consult_id] = message

    return Page(
        items=[
            ConsultOut(
                id=consult.id,
                status=consult.status,
                service=ConsultServiceRef(
                    id=service.id, name=service.name, service_type=service.service_type
                ),
                provider=ConsultProviderRef(
                    id=provider.id,
                    name=provider.name,
                    title=provider.title,
                    avatar=provider.avatar,
                    online=bool(provider.online),
                ),
                last_message=(
                    make_excerpt(last_messages[consult.id].content, 60)
                    if consult.id in last_messages
                    else None
                ),
                last_message_at=consult.last_message_at,
                created_at=consult.created_at,
            )
            for consult, service, provider in rows
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


async def _get_own_consult(db: AsyncSession, user: User, consult_id: int) -> MpConsult:
    consult = await db.get(MpConsult, consult_id)
    if consult is None or consult.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return consult


@router.get("/consults/{consult_id}/messages", response_model=list[ConsultMessageOut])
async def list_consult_messages(
    consult_id: int,
    after_id: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConsultMessageOut]:
    consult = await _get_own_consult(db, user, consult_id)
    rows = (
        await db.execute(
            select(MpConsultMessage)
            .where(
                MpConsultMessage.consult_id == consult.id,
                MpConsultMessage.id > after_id,
            )
            .order_by(MpConsultMessage.id.asc())
        )
    ).scalars().all()
    return [
        ConsultMessageOut(
            id=message.id,
            sender=message.sender,
            content=message.content,
            created_at=message.created_at,
        )
        for message in rows
    ]


@router.post("/consults/{consult_id}/messages", status_code=201)
async def send_consult_message(
    consult_id: int,
    body: ConsultMessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    _require_consult_demo_open()
    await check_user_rate_limit(redis, user_id=user.id, action="mp.messages", limit=20)
    consult = await _get_own_consult(db, user, consult_id)
    if consult.status != MpConsultStatus.OPEN.value:
        raise HTTPException(status_code=409, detail="会话已结束，无法继续发送消息")
    content = validate_content(body.content)

    service = await db.get(MpService, consult.service_id)
    provider = await db.get(MpProvider, consult.provider_id)
    user_message, reply_message = await append_user_message_and_reply(
        db, consult, content, service, provider
    )
    await db.commit()

    def _message_out(message: MpConsultMessage) -> ConsultMessageOut:
        return ConsultMessageOut(
            id=message.id,
            sender=message.sender,
            content=message.content,
            created_at=message.created_at,
        )

    return {
        "user_message": _message_out(user_message).model_dump(mode="json"),
        "system_message": _message_out(reply_message).model_dump(mode="json"),
    }
