"""采购单位查看端（scope=org）：登录、本单位批次汇总。

单位默认只能看采购与发放计数汇总，看不到任何手机号、姓名或健康信息；
越权访问（user/admin token 打 /org，或 org token 打 /mp /admin）由 scope 校验拒绝。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_org_account, get_db, pagination_params
from app.core.security import SCOPE_ORG, create_token
from app.models import BatchRowStatus, MpBatchRow, MpOrg, MpOrgAccount, MpPackage, MpPackageVersion, MpPurchaseBatch
from app.schemas.common import Page
from app.schemas.commerce import (
    BatchPackageRef,
    BatchSummary,
    OrgAccountBrief,
    OrgBatchOut,
    OrgBrief,
    OrgLoginIn,
    OrgLoginOut,
    OrgMeOut,
)
from app.services import commerce_service as svc

router = APIRouter(prefix="/org", tags=["org"])


def _row_status_counts(db_rows: list[MpBatchRow]) -> BatchSummary:
    return BatchSummary(
        total=len(db_rows),
        valid=sum(1 for r in db_rows if r.status == BatchRowStatus.VALID.value),
        duplicate=sum(1 for r in db_rows if r.status == BatchRowStatus.DUPLICATE.value),
        format_error=sum(1 for r in db_rows if r.status == BatchRowStatus.FORMAT_ERROR.value),
        over_limit=sum(1 for r in db_rows if r.status == BatchRowStatus.OVER_LIMIT.value),
    )


async def _batch_out(db: AsyncSession, batch: MpPurchaseBatch, rows: list[MpBatchRow]) -> OrgBatchOut:
    version = await db.get(MpPackageVersion, batch.package_version_id)
    package = await db.get(MpPackage, batch.package_id)
    counts = await svc.batch_status_counts(db, batch.id)
    return OrgBatchOut(
        id=batch.id,
        batch_no=batch.batch_no,
        package=BatchPackageRef(
            id=package.id, name=package.name, version_no=version.version_no
        ),
        quantity=batch.quantity,
        valid_count=sum(1 for r in rows if r.status == BatchRowStatus.VALID.value),
        issued_count=counts["issued"],
        claimed_count=counts["claimed"],
        rejected_count=counts["rejected"],
        pending_count=counts["pending"],
        status=batch.status,
        confirmed_at=batch.confirmed_at,
        created_at=batch.created_at,
    )


@router.post("/auth/login", response_model=OrgLoginOut)
async def org_login(
    body: OrgLoginIn, db: AsyncSession = Depends(get_db)
) -> OrgLoginOut:
    account, org = await svc.authenticate_org_account(db, body.username, body.password)
    return OrgLoginOut(
        access_token=create_token(account.id, SCOPE_ORG, "access"),
        refresh_token=create_token(account.id, SCOPE_ORG, "refresh"),
        org=OrgBrief(id=org.id, name=org.name),
        account=OrgAccountBrief(id=account.id, username=account.username),
    )


@router.get("/me", response_model=OrgMeOut)
async def org_me(
    principal: tuple[MpOrgAccount, MpOrg] = Depends(get_current_org_account),
) -> OrgMeOut:
    account, org = principal
    return OrgMeOut(
        org=OrgBrief(id=org.id, name=org.name),
        account=OrgAccountBrief(id=account.id, username=account.username),
    )


@router.get("/batches", response_model=Page[OrgBatchOut])
async def org_batches(
    pagination: tuple[int, int] = Depends(pagination_params),
    principal: tuple[MpOrgAccount, MpOrg] = Depends(get_current_org_account),
    db: AsyncSession = Depends(get_db),
) -> Page[OrgBatchOut]:
    _, org = principal
    page, page_size = pagination
    total = (
        await db.execute(
            select(func.count()).select_from(MpPurchaseBatch).where(MpPurchaseBatch.org_id == org.id)
        )
    ).scalar()
    batches = (
        await db.execute(
            select(MpPurchaseBatch)
            .where(MpPurchaseBatch.org_id == org.id)
            .order_by(MpPurchaseBatch.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    items = []
    for batch in batches:
        rows = (
            await db.execute(select(MpBatchRow).where(MpBatchRow.batch_id == batch.id))
        ).scalars().all()
        items.append(await _batch_out(db, batch, rows))
    return Page(items=items, total=int(total or 0), page=page, page_size=page_size)


@router.get("/batches/{batch_id}", response_model=OrgBatchOut)
async def org_batch_detail(
    batch_id: int,
    principal: tuple[MpOrgAccount, MpOrg] = Depends(get_current_org_account),
    db: AsyncSession = Depends(get_db),
) -> OrgBatchOut:
    _, org = principal
    batch = await svc.org_batch_guard(db, org_id=org.id, batch_id=batch_id)
    rows = (
        await db.execute(select(MpBatchRow).where(MpBatchRow.batch_id == batch.id))
    ).scalars().all()
    return await _batch_out(db, batch, rows)
