"""后台角色权限点：ROLE_PERMISSIONS 矩阵 + require_permission 依赖。

矩阵以契约 docs/api.md「后台角色权限矩阵」为唯一权威；新增权限点时先改契约再改这里。
设计参考 ClinInsOS 的 permission-point 模式（super_admin/admin/viewer × ~23 点），
当前规模收敛为 admin/editor 两角色 × 7 点，后续加角色（如 viewer 只读）只需扩矩阵。
"""

from fastapi import Depends, HTTPException

from app.models import AdminUser
from app.core.deps import get_current_admin

ESSAY_REVIEW = "essay.review"
ESSAY_FEATURE = "essay.feature"
ESSAY_OFFLINE = "essay.offline"
ESSAY_EXPORT = "essay.export"
CONTEST_MANAGE = "contest.manage"
CONTEST_DELETE = "contest.delete"
USER_MANAGE = "user.manage"
AUDIT_READ = "audit.read"
MP_MANAGE = "mp.manage"
OCCUPATIONAL_READ = "occupational.read"
# 两级评审（2026-10-08 方案 B）：初审编辑可参与，终审仅管理员且不得与初审同一账号
OCCUPATIONAL_INITIAL_REVIEW = "occupational.initial_review"
OCCUPATIONAL_FINAL_REVIEW = "occupational.final_review"
VOLUNTEER_READ = "volunteer.read"
# 服务包交易（工作一，2026-10-21）：商品维护与查询对编辑开放；动钱、发权益、调积分仅管理员
COMMERCE_PRODUCT_MANAGE = "commerce.product.manage"
COMMERCE_ORDER_READ = "commerce.order.read"
COMMERCE_ORDER_REFUND = "commerce.order.refund"
COMMERCE_POINTS_ADJUST = "commerce.points.adjust"
COMMERCE_BATCH_MANAGE = "commerce.batch.manage"
COMMERCE_BATCH_CONFIRM = "commerce.batch.confirm"
# 服务履约（工作二，2026-10-22）：查询对编辑开放；门禁录入 / 指派 / 暂停 / 结案等仅管理员
STEWARD_READ = "steward.read"
STEWARD_MANAGE = "steward.manage"
# 触达工作台（工作二切片 B）：查询对编辑开放；流转与交接仅管理员
OUTREACH_READ = "outreach.read"
OUTREACH_MANAGE = "outreach.manage"

_ALL = {
    ESSAY_REVIEW,
    ESSAY_FEATURE,
    ESSAY_OFFLINE,
    ESSAY_EXPORT,
    CONTEST_MANAGE,
    CONTEST_DELETE,
    USER_MANAGE,
    AUDIT_READ,
    MP_MANAGE,
    OCCUPATIONAL_READ,
    OCCUPATIONAL_INITIAL_REVIEW,
    OCCUPATIONAL_FINAL_REVIEW,
    VOLUNTEER_READ,
    COMMERCE_PRODUCT_MANAGE,
    COMMERCE_ORDER_READ,
    COMMERCE_ORDER_REFUND,
    COMMERCE_POINTS_ADJUST,
    COMMERCE_BATCH_MANAGE,
    COMMERCE_BATCH_CONFIRM,
    STEWARD_READ,
    STEWARD_MANAGE,
    OUTREACH_READ,
    OUTREACH_MANAGE,
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": set(_ALL),
    # 编辑：内容运营（征文审核 + 健康域运营 + 职业健康形式初审 + 服务包商品维护/查询/名单预览），
    # 不碰账号封禁、删活动、审计、职业健康终审、退款、积分调整、批次确认发放
    "editor": {
        ESSAY_REVIEW,
        ESSAY_FEATURE,
        ESSAY_OFFLINE,
        ESSAY_EXPORT,
        CONTEST_MANAGE,
        MP_MANAGE,
        OCCUPATIONAL_READ,
        OCCUPATIONAL_INITIAL_REVIEW,
        COMMERCE_PRODUCT_MANAGE,
        COMMERCE_ORDER_READ,
        COMMERCE_BATCH_MANAGE,
        STEWARD_READ,
        OUTREACH_READ,
    },
}


def role_has(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


def require_permission(permission: str):
    """依赖工厂：校验当前后台账号的角色持有权限点，否则 403。"""

    async def _checker(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if not role_has(admin.role, permission):
            raise HTTPException(status_code=403, detail="无权限执行该操作")
        return admin

    return _checker
