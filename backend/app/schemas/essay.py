"""征文作品 DTO（后台与 C 端共用列表/详情结构）。"""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from app.schemas.common import UTCDateTime, make_excerpt

if TYPE_CHECKING:  # 仅类型提示用，避免运行时反向依赖
    from app.models.contest import Contest
    from app.models.essay import Essay
    from app.models.user import User


class EssayAuthor(BaseModel):
    id: int
    nickname: str
    avatar: str | None


class EssayContestRef(BaseModel):
    id: int
    title: str


class EssayListItem(BaseModel):
    id: int
    title: str
    excerpt: str
    author: EssayAuthor
    anonymous: bool
    contest: EssayContestRef
    status: str
    score: int | None
    featured: bool
    like_count: int
    view_count: int
    created_at: UTCDateTime


class EssayDetail(EssayListItem):
    content: str
    liked_by_me: bool = False


class EssayReviewOut(BaseModel):
    id: int
    admin_name: str
    action: str
    score: int | None
    comment: str | None
    created_at: UTCDateTime


class EssayAdminDetail(EssayDetail):
    reviews: list[EssayReviewOut] = []


class EssayCreate(BaseModel):
    contest_id: int
    title: str
    content: str
    anonymous: bool = False


class LikeResponse(BaseModel):
    liked: bool
    like_count: int


def essay_common_fields(essay: "Essay", author: "User", contest: "Contest") -> dict[str, Any]:
    """列表/详情共用的字段构造；anonymous 时作者信息脱敏。"""
    anonymous = bool(essay.anonymous)
    return {
        "id": essay.id,
        "title": essay.title,
        "excerpt": make_excerpt(essay.content),
        "author": {
            "id": author.id,
            "nickname": "匿名" if anonymous else author.nickname,
            "avatar": None if anonymous else author.avatar,
        },
        "anonymous": anonymous,
        "contest": {"id": contest.id, "title": contest.title},
        "status": essay.status,
        "score": essay.score,
        "featured": bool(essay.featured),
        "like_count": essay.like_count,
        "view_count": essay.view_count,
        "created_at": essay.created_at,
    }
