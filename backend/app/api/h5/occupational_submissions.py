"""公开职业健康科普投稿：无短信依赖，私有文件，失败关闭。"""

import hashlib
import hmac
import re
import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_db, get_redis
from app.models import OccupationalSubmission
from app.schemas.occupational import OccupationalReceipt, OccupationalReceiptStatus, WorkType
from app.services.occupational_submission_service import (
    ARTICLE_MAX_BYTES,
    TOPICS,
    VIDEO_MAX_BYTES,
    VIDEO_MIN_LONG_EDGE,
    VIDEO_MIN_SHORT_EDGE,
    hash_uploaded_work,
    private_submission_dir,
    save_and_validate_work,
)

router = APIRouter(prefix="/h5/occupational-submissions", tags=["h5-occupational-submissions"])
MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
LANDLINE_RE = re.compile(r"^0\d{2,3}-[1-9]\d{6,7}(?:[-#转]\d{1,6})?$")
CONSENT_TEXT = "我同意仅为本次职业健康科普作品征集的评审与联系使用本人姓名、工作单位、联系电话及作品信息。"


def _clean(value: str, label: str, maximum: int, minimum: int = 1) -> str:
    cleaned = value.strip()
    if not minimum <= len(cleaned) <= maximum:
        raise HTTPException(status_code=422, detail=f"{label}长度需在 {minimum} 至 {maximum} 字之间")
    return cleaned


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in {"127.0.0.1", "::1"}:
        # 仅信任本机反代提供的地址；公网客户端不能自行伪造限流键。
        forwarded = request.headers.get("x-real-ip")
        if forwarded:
            return forwarded[:64]
    return peer


def _assert_replay_fields(
    existing: OccupationalSubmission,
    *,
    phone: str,
    work_type: str,
    title: str,
    topic: str,
    author_name: str,
    organization: str,
    summary: str,
) -> None:
    if existing.phone != phone:
        raise HTTPException(status_code=409, detail="该提交标识已被其他联系电话使用")
    if any((
        existing.work_type != work_type,
        existing.title != title,
        existing.topic != topic,
        existing.author_name != author_name,
        existing.organization != organization,
        existing.summary != summary,
    )):
        raise HTTPException(status_code=409, detail="该提交标识对应的表单内容已变更，请重新生成提交标识")


def _replay_receipt(
    existing: OccupationalSubmission,
    *,
    response: Response,
    file_sha256: str,
    phone: str,
    work_type: str,
    title: str,
    topic: str,
    author_name: str,
    organization: str,
    summary: str,
) -> OccupationalReceipt:
    _assert_replay_fields(
        existing, phone=phone, work_type=work_type, title=title, topic=topic,
        author_name=author_name, organization=organization, summary=summary,
    )
    if existing.file_sha256 != file_sha256:
        raise HTTPException(status_code=409, detail="该提交标识对应的文件已变更，请重新生成提交标识")
    response.status_code = 200
    return OccupationalReceipt(
        id=existing.id, receipt_no=existing.receipt_no,
        status="submitted", submitted_at=existing.created_at,
    )


async def _limit(redis: Redis, key: str, maximum: int, window_seconds: int) -> None:
    try:
        count = await redis.incr(key)
        if count == 1:
            if not await redis.expire(key, window_seconds):
                raise RuntimeError("限流窗口未设置成功")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="投稿限流服务暂不可用，请稍后再试") from exc
    if count > maximum:
        raise HTTPException(status_code=429, detail="投稿过于频繁，请稍后再试")


@router.get("/meta")
async def submission_meta() -> dict:
    return {
        "enabled": settings.OCCUPATIONAL_SUBMISSIONS_ENABLED,
        "accepted_work_types": ["article", "video"],
        "topics": list(TOPICS),
        "accepts_other_topic": True,
        "consent_text": CONSENT_TEXT,
        "limits": {
            "article_max_bytes": ARTICLE_MAX_BYTES,
            "video_max_bytes": VIDEO_MAX_BYTES,
            "video_min_short_edge": VIDEO_MIN_SHORT_EDGE,
            "video_min_long_edge": VIDEO_MIN_LONG_EDGE,
        },
    }


RECEIPT_NO_RE = re.compile(r"^OCC-[0-9A-F]{16}$")


@router.get("/receipt/{receipt_no}", response_model=OccupationalReceiptStatus)
async def query_receipt(
    receipt_no: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> OccupationalReceiptStatus:
    """公开回执查询：仅状态与驳回理由，不含联系方式、单位或文件信息。"""
    await _limit(redis, f"occupational:receipt:ip:{_client_ip(request)}", 600, 3600)
    normalized = receipt_no.strip().upper()
    if not RECEIPT_NO_RE.fullmatch(normalized):
        raise HTTPException(status_code=404, detail="回执号不存在")
    row = (
        await db.execute(
            select(OccupationalSubmission).where(OccupationalSubmission.receipt_no == normalized)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="回执号不存在")
    rejected = row.status == "rejected"
    return OccupationalReceiptStatus(
        receipt_no=row.receipt_no,
        title=row.title,
        work_type=row.work_type,
        status=row.status,
        submitted_at=row.created_at,
        decided_at=row.reviewed_at if row.status in {"accepted", "rejected"} else None,
        comment=row.review_comment if rejected else None,
    )


@router.post("", response_model=OccupationalReceipt, status_code=201)
async def create_occupational_submission(
    request: Request,
    response: Response,
    client_submission_id: UUID = Form(...),
    work_type: WorkType = Form(...),
    title: str = Form(...),
    topic: str = Form(...),
    author_name: str = Form(...),
    organization: str = Form(...),
    phone: str = Form(..., max_length=20),
    summary: str = Form(...),
    consent_review_contact: bool = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> OccupationalReceipt:
    if not settings.OCCUPATIONAL_SUBMISSIONS_ENABLED:
        raise HTTPException(status_code=503, detail="职业健康作品投稿暂未开放")
    if consent_review_contact is not True:
        raise HTTPException(status_code=422, detail="请先确认评审联系所需的个人信息使用说明")
    title = _clean(title, "作品标题", 100)
    topic = _clean(topic, "选题", 100)
    author_name = _clean(author_name, "作者姓名", 50)
    organization = _clean(organization, "工作单位", 100)
    phone = phone.strip()
    if not (MOBILE_RE.fullmatch(phone) or LANDLINE_RE.fullmatch(phone)):
        raise HTTPException(status_code=422, detail="联系电话需为大陆手机号或带区号座机（可含分机）")
    summary = _clean(summary, "作品简介", 1000, 10)
    client_id = str(client_submission_id)
    existing = (
        await db.execute(
            select(OccupationalSubmission).where(
                OccupationalSubmission.client_submission_id == client_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        try:
            _assert_replay_fields(
                existing, phone=phone, work_type=work_type, title=title, topic=topic,
                author_name=author_name, organization=organization, summary=summary,
            )
            file_sha256 = await hash_uploaded_work(file, work_type)
        finally:
            await file.close()
        return _replay_receipt(
            existing, response=response, file_sha256=file_sha256, phone=phone,
            work_type=work_type, title=title,
            topic=topic, author_name=author_name, organization=organization, summary=summary,
        )

    phone_digest = hmac.new(settings.JWT_SECRET.encode(), phone.encode(), hashlib.sha256).hexdigest()[:24]
    ip_limit, phone_limit = (6, 6) if work_type == "video" else (30, 10)
    await _limit(redis, f"occupational:submit:{work_type}:ip:{_client_ip(request)}", ip_limit, 3600)
    await _limit(redis, f"occupational:submit:{work_type}:phone:{phone_digest}", phone_limit, 86400)

    saved = await save_and_validate_work(file, work_type)
    now = datetime.now(timezone.utc)
    row = OccupationalSubmission(
        receipt_no=f"OCC-{uuid.uuid4().hex[:16].upper()}",
        client_submission_id=client_id,
        work_type=work_type,
        title=title,
        topic=topic,
        author_name=author_name,
        organization=organization,
        phone=phone,
        summary=summary,
        status="submitted",
        consent_review_contact=True,
        consent_text=CONSENT_TEXT,
        consented_at=now,
        contact_ip=_client_ip(request),
        stored_filename=saved.stored_filename,
        original_filename=saved.original_filename,
        file_size=saved.file_size,
        file_sha256=saved.file_sha256,
        word_count=saved.word_count,
        duration_seconds=saved.duration_seconds,
        video_width=saved.video_width,
        video_height=saved.video_height,
    )
    try:
        db.add(row)
        await db.flush()
        await db.refresh(row)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        (private_submission_dir() / saved.stored_filename).unlink(missing_ok=True)
        winner = (
            await db.execute(
                select(OccupationalSubmission).where(
                    OccupationalSubmission.client_submission_id == client_id
                )
            )
        ).scalar_one_or_none()
        if winner is None:
            raise exc
        return _replay_receipt(
            winner, response=response, file_sha256=saved.file_sha256, phone=phone,
            work_type=work_type, title=title,
            topic=topic, author_name=author_name, organization=organization, summary=summary,
        )
    except Exception:
        await db.rollback()
        (private_submission_dir() / saved.stored_filename).unlink(missing_ok=True)
        raise
    return OccupationalReceipt(
        id=row.id, receipt_no=row.receipt_no, status=row.status, submitted_at=row.created_at
    )
