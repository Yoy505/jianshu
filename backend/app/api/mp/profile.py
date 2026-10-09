"""小程序 · 健康档案（五步闭环·第五步）：查看与整体提交建档/更新。"""

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user, get_db
from app.models import MpProfile, User
from app.schemas.mp import ProfileOut, ProfileSave

router = APIRouter()

GENDERS = ("male", "female")
BIRTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
BLOODS = ("A", "B", "O", "AB", "Rh+", "Rh-")
PROFILE_CLOSED_MESSAGE = (
    "健康档案的敏感信息单独同意、版本记录与删除流程尚未上线，"
    "当前暂停新建和修改；已有档案仍可本人查看。"
)


def _profile_write_mode() -> str:
    return "demo" if settings.APP_ENV in {"dev", "test"} else "closed"


@router.get("/profile/capabilities", response_model=dict[str, str])
async def profile_capabilities(
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    if _profile_write_mode() == "demo":
        return {
            "write_mode": "demo",
            "message": "仅供开发/测试演示，请勿填写真实健康信息。",
        }
    return {"write_mode": "closed", "message": PROFILE_CLOSED_MESSAGE}


@router.get("/profile", response_model=ProfileOut | None)
async def get_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut | None:
    profile = await db.get(MpProfile, user.id)
    if profile is None:
        return None
    return _profile_out(profile)


@router.put("/profile", response_model=ProfileOut)
async def save_profile(
    body: ProfileSave,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    if _profile_write_mode() != "demo":
        raise HTTPException(status_code=503, detail=PROFILE_CLOSED_MESSAGE)

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    if body.gender not in GENDERS:
        raise HTTPException(status_code=400, detail="性别取值需为 male 或 female")
    if not BIRTH_RE.match(body.birth or ""):
        raise HTTPException(status_code=400, detail="出生年月格式应为 YYYY-MM")
    if body.height_cm is not None and not 30 <= body.height_cm <= 260:
        raise HTTPException(status_code=400, detail="身高数值不正确")
    if body.weight_kg is not None and not 2 <= body.weight_kg <= 300:
        raise HTTPException(status_code=400, detail="体重数值不正确")
    if body.blood is not None and body.blood not in BLOODS:
        raise HTTPException(status_code=400, detail="血型取值不正确")
    chronic_tags = [tag.strip() for tag in (body.chronic_tags or []) if tag.strip()]
    if any(len(tag) > 20 for tag in chronic_tags):
        raise HTTPException(status_code=400, detail="单个慢病标签不能超过 20 字")

    profile = await db.get(MpProfile, user.id)
    if profile is None:
        profile = MpProfile(user_id=user.id)
        db.add(profile)
    profile.name = name
    profile.gender = body.gender
    profile.birth = body.birth
    profile.height_cm = body.height_cm
    profile.weight_kg = body.weight_kg
    profile.blood = body.blood
    profile.chronic_tags = chronic_tags
    profile.allergy = (body.allergy or "").strip() or None
    profile.surgery_history = (body.surgery_history or "").strip() or None
    profile.smoke = bool(body.smoke)
    profile.drink = bool(body.drink)
    profile.sport_freq = (body.sport_freq or "").strip() or None

    await db.commit()
    return _profile_out(profile)


def _profile_out(profile: MpProfile) -> ProfileOut:
    return ProfileOut(
        name=profile.name,
        gender=profile.gender,
        birth=profile.birth,
        height_cm=profile.height_cm,
        weight_kg=float(profile.weight_kg) if profile.weight_kg is not None else None,
        blood=profile.blood,
        chronic_tags=list(profile.chronic_tags or []),
        allergy=profile.allergy,
        surgery_history=profile.surgery_history,
        smoke=bool(profile.smoke),
        drink=bool(profile.drink),
        sport_freq=profile.sport_freq,
    )
