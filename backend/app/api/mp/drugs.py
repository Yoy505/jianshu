"""小程序 · 药品数字展厅：检索（关键词/标签/首字母）、详情、收藏、留言。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, get_current_user_optional, get_db, pagination_params
from app.models import MP_DRUG_TAGS, MpDrug, MpDrugFeedback, MpFavorite, User
from app.schemas.common import Page
from app.schemas.mp import (
    DrugBoardItemOut,
    DrugDetailOut,
    DrugFeedbackCreate,
    DrugOut,
    FavoriteToggleOut,
)

router = APIRouter()

FEEDBACK_MIN, FEEDBACK_MAX = 20, 500


def _feedback_mode() -> str:
    """没有资质核验和内容审核流程，公开环境关闭留言。"""
    return "demo" if settings.APP_ENV in ("dev", "test") else "closed"


def _public_statuses() -> tuple[str, ...]:
    """Legacy/demo data must never leak from staging or production."""
    return ("demo", "published") if settings.APP_ENV in {"dev", "test"} else ("published",)


def _is_public(drug: MpDrug | None) -> bool:
    return drug is not None and drug.publication_status in _public_statuses()


def _drug_out(drug: MpDrug, favorited: bool = False) -> DrugOut:
    return DrugOut(
        id=drug.id,
        common_name=drug.common_name,
        approval_no=drug.approval_no,
        specification=drug.specification,
        license_holder=drug.license_holder,
        source_name=drug.source_name,
        source_url=drug.source_url,
        source_checked_at=drug.source_checked_at,
        publication_status=drug.publication_status,
        form=drug.form,
        manufacturer=drug.manufacturer,
        tags=list(drug.tags or []),
        indication=drug.indication,
        usage_note=drug.usage_note,
        contraindication=drug.contraindication,
        science=drug.science,
        video_url=drug.video_url,
        # 品牌展示缺少来源与广告审核状态，正式环境先不对外发布。
        brand_name=(
            drug.brand_name
            if drug.publication_status == "demo" and settings.APP_ENV in {"dev", "test"}
            else None
        ),
        favorited=favorited,
    )


async def _favorited_drug_ids(db: AsyncSession, user_id: int | None) -> set[int]:
    if user_id is None:
        return set()
    rows = (
        await db.execute(select(MpFavorite.drug_id).where(MpFavorite.user_id == user_id))
    ).scalars().all()
    return set(rows)


@router.get("/drugs", response_model=Page[DrugOut])
async def list_drugs(
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    letter: str | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    viewer: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> Page[DrugOut]:
    page, page_size = pagination
    if tag is not None and tag not in MP_DRUG_TAGS:
        raise HTTPException(status_code=400, detail="药品标签仅支持 otc、rx、essential 或 insurance")
    if letter is not None and not (len(letter) == 1 and "A" <= letter <= "Z"):
        raise HTTPException(status_code=400, detail="letter 需为大写字母 A-Z")

    filters: list = [MpDrug.publication_status.in_(_public_statuses())]
    if q:
        keyword = f"%{q.strip()}%"
        name_filter = MpDrug.common_name.like(keyword)
        if settings.APP_ENV in {"dev", "test"}:
            name_filter |= (
                (MpDrug.publication_status == "demo") & MpDrug.brand_name.like(keyword)
            )
        filters.append(name_filter)
    if tag is not None:
        # tags 为 JSON 数组，按文本包含匹配子集标签
        filters.append(cast(MpDrug.tags, String).like(f'%"{tag}"%'))
    if letter is not None:
        filters.append(MpDrug.initial == letter)

    total = await db.scalar(select(func.count()).select_from(MpDrug).where(*filters))
    rows = (
        await db.execute(
            select(MpDrug)
            .where(*filters)
            .order_by(MpDrug.common_name.asc(), MpDrug.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    favorited_ids = await _favorited_drug_ids(db, viewer.id if viewer else None)
    return Page(
        items=[_drug_out(drug, drug.id in favorited_ids) for drug in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/drugs/{drug_id}", response_model=DrugDetailOut)
async def get_drug(
    drug_id: int,
    viewer: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> DrugDetailOut:
    drug = await db.get(MpDrug, drug_id)
    if not _is_public(drug):
        raise HTTPException(status_code=404, detail="药品不存在")

    favorited_ids = await _favorited_drug_ids(db, viewer.id if viewer else None)
    feedback_mode = (
        "demo"
        if drug.publication_status == "demo" and settings.APP_ENV in {"dev", "test"}
        else "closed"
    )
    board_rows = []
    if feedback_mode == "demo":
        board_rows = (
            await db.execute(
                select(MpDrugFeedback, User.nickname)
                .join(User, MpDrugFeedback.user_id == User.id)
                .where(MpDrugFeedback.drug_id == drug.id)
                .order_by(MpDrugFeedback.created_at.desc(), MpDrugFeedback.id.desc())
                .limit(10)
            )
        ).all()

    return DrugDetailOut(
        **_drug_out(drug, drug.id in favorited_ids).model_dump(),
        feedback_mode=feedback_mode,
        board=[
            DrugBoardItemOut(
                id=feedback.id,
                user_name=nickname or "匿名用户",
                content=feedback.content,
                created_at=feedback.created_at,
            )
            for feedback, nickname in board_rows
        ],
    )


@router.post("/drugs/{drug_id}/favorite", response_model=FavoriteToggleOut)
async def toggle_favorite(
    drug_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FavoriteToggleOut:
    drug = await db.get(MpDrug, drug_id)
    if not _is_public(drug):
        raise HTTPException(status_code=404, detail="药品不存在")

    existing = (
        await db.execute(
            select(MpFavorite).where(MpFavorite.drug_id == drug.id, MpFavorite.user_id == user.id)
        )
    ).scalar_one_or_none()

    if existing is not None:
        await db.delete(existing)
        favorited = False
    else:
        db.add(MpFavorite(drug_id=drug.id, user_id=user.id))
        favorited = True
    await db.commit()
    return FavoriteToggleOut(favorited=favorited)


@router.post("/drugs/{drug_id}/feedback", status_code=201)
async def create_drug_feedback(
    drug_id: int,
    body: DrugFeedbackCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    drug = await db.get(MpDrug, drug_id)
    if not _is_public(drug):
        raise HTTPException(status_code=404, detail="药品不存在")
    if _feedback_mode() != "demo" or drug.publication_status != "demo":
        raise HTTPException(status_code=503, detail="留言功能暂未开放：医师资质核验与内容审核尚未上线")

    content = (body.content or "").strip()
    if not FEEDBACK_MIN <= len(content) <= FEEDBACK_MAX:
        raise HTTPException(status_code=400, detail=f"留言内容需在 {FEEDBACK_MIN} 至 {FEEDBACK_MAX} 字之间")

    feedback = MpDrugFeedback(drug_id=drug.id, user_id=user.id, content=content)
    db.add(feedback)
    await db.commit()
    return {"id": feedback.id}


@router.get("/favorites", response_model=Page[DrugOut])
async def list_favorites(
    pagination: tuple[int, int] = Depends(pagination_params),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Page[DrugOut]:
    page, page_size = pagination
    public_filter = MpDrug.publication_status.in_(_public_statuses())
    total = await db.scalar(
        select(func.count())
        .select_from(MpFavorite)
        .join(MpDrug, MpFavorite.drug_id == MpDrug.id)
        .where(MpFavorite.user_id == user.id, public_filter)
    )
    rows = (
        await db.execute(
            select(MpDrug)
            .join(MpFavorite, MpFavorite.drug_id == MpDrug.id)
            .where(MpFavorite.user_id == user.id, public_filter)
            .order_by(MpFavorite.created_at.desc(), MpFavorite.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return Page(
        items=[_drug_out(drug, favorited=True) for drug in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )
