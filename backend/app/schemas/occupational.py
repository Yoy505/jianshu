"""职业健康科普作品征集 DTO；公开回执不包含联系方式或存储路径。"""

from pydantic import BaseModel, Field
from typing import Literal
import re

from app.models.occupational import OccupationalSubmission
from app.schemas.common import UTCDateTime, mask_phone

WorkType = Literal["article", "video"]
InitialAction = Literal["claim", "release", "pass", "reject"]
FinalAction = Literal["pass", "reject"]
ReviewStage = Literal["initial", "final"]
ReviewConclusion = Literal["passed", "rejected"]

STATUS_SUBMITTED = "submitted"
STATUS_INITIAL_REVIEWING = "initial_reviewing"
STATUS_PENDING_FINAL = "pending_final"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
REVIEW_STATUSES = {
    STATUS_SUBMITTED,
    STATUS_INITIAL_REVIEWING,
    STATUS_PENDING_FINAL,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
}


class OccupationalReceipt(BaseModel):
    id: int
    receipt_no: str
    status: str
    submitted_at: UTCDateTime


class OccupationalReceiptStatus(BaseModel):
    """公开回执查询：不含联系方式、单位、文件信息。"""

    receipt_no: str
    title: str
    work_type: WorkType
    status: str
    submitted_at: UTCDateTime
    decided_at: UTCDateTime | None = None
    comment: str | None = None


class OccupationalListItem(BaseModel):
    id: int
    receipt_no: str
    work_type: WorkType
    title: str
    topic: str
    author_name: str
    organization: str
    phone_masked: str | None
    status: str
    submitted_at: UTCDateTime
    word_count: int | None
    duration_seconds: float | None


class OccupationalReviewRecordOut(BaseModel):
    stage: ReviewStage
    reviewer_id: int
    reviewer_name: str
    conclusion: ReviewConclusion
    comment: str | None
    score: int | None
    created_at: UTCDateTime


class OccupationalLockInfo(BaseModel):
    """初审软锁状态：expired 由服务端按 30 分钟窗口计算。"""

    locked_by_admin_id: int | None
    locked_by_name: str | None
    locked_at: UTCDateTime | None
    expired: bool


class OccupationalDetail(OccupationalListItem):
    client_submission_id: str
    phone: str
    summary: str
    original_filename: str
    file_size: int
    file_sha256: str
    video_width: int | None
    video_height: int | None
    consent_review_contact: bool
    consent_text: str
    consented_at: UTCDateTime
    review_comment: str | None
    reviewed_by: int | None
    reviewed_at: UTCDateTime | None
    reviews: list[OccupationalReviewRecordOut]
    lock: OccupationalLockInfo


class OccupationalInitialReviewRequest(BaseModel):
    action: InitialAction
    comment: str | None = Field(default=None, max_length=1000)


class OccupationalFinalReviewRequest(BaseModel):
    action: FinalAction
    comment: str | None = Field(default=None, max_length=1000)
    score: int | None = Field(default=None, ge=0, le=100)


def submission_list_item(row: OccupationalSubmission) -> OccupationalListItem:
    landline = re.fullmatch(r"(0\d{2,3})-([1-9]\d{6,7})(?:[-#转](\d{1,6}))?", row.phone)
    masked_contact = (
        f"{landline.group(1)}-****{landline.group(2)[-4:]}"
        + ("-****" if landline.group(3) else "")
        if landline else mask_phone(row.phone)
    )
    return OccupationalListItem(
        id=row.id,
        receipt_no=row.receipt_no,
        work_type=row.work_type,
        title=row.title,
        topic=row.topic,
        author_name=row.author_name,
        organization=row.organization,
        phone_masked=masked_contact,
        status=row.status,
        submitted_at=row.created_at,
        word_count=row.word_count,
        duration_seconds=row.duration_seconds,
    )


def submission_detail(
    row: OccupationalSubmission,
    *,
    reviews: list[OccupationalReviewRecordOut] | None = None,
    lock: OccupationalLockInfo | None = None,
) -> OccupationalDetail:
    return OccupationalDetail(
        **submission_list_item(row).model_dump(),
        client_submission_id=row.client_submission_id,
        phone=row.phone,
        summary=row.summary,
        original_filename=row.original_filename,
        file_size=row.file_size,
        file_sha256=row.file_sha256,
        video_width=row.video_width,
        video_height=row.video_height,
        consent_review_contact=row.consent_review_contact,
        consent_text=row.consent_text,
        consented_at=row.consented_at,
        review_comment=row.review_comment,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        reviews=reviews or [],
        lock=lock
        or OccupationalLockInfo(
            locked_by_admin_id=None, locked_by_name=None, locked_at=None, expired=False
        ),
    )
