"""服务层公共工具：昵称生成、用户序列化。"""

import random

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.schemas.common import mask_phone
from app.schemas.user import UserOut

NICKNAME_POOL = [
    "青山客", "南屿", "杏林友", "云间客", "栖迟", "白露生", "星野",
    "晚风急", "听松人", "渡口", "灯下客", "拾星", "望舒", "山月客",
    "临江仙", "枕流", "照夜白", "半夏", "疏影", "拾云",
]


def generate_nickname() -> str:
    """随机中文昵称 + 2 位数字，如「青山客37」。"""
    return f"{random.choice(NICKNAME_POOL)}{random.randint(10, 99)}"


async def serialize_user(db: AsyncSession, user: User) -> UserOut:
    """输出 C 端用户对象（含掩码手机号）。"""
    return UserOut(
        id=user.id,
        nickname=user.nickname,
        avatar=user.avatar,
        phone=mask_phone(user.phone),
        status=user.status,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )
