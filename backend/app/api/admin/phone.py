"""后台 · 电话通路接口（工作二切片 E，契约 docs/api.md 第五部分 · 十）。

权限：phone.read（编辑含）/ phone.manage（仅管理员）。真实外呼保持关闭——
无适配器时工单待配置；回调须验签（密钥未配置即拒收）。
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, pagination_params
from app.core.permissions import require_permission
from app.models import PhoneCallAttempt, PhoneCallOrder, PhoneContact, PhoneScript
from app.schemas.common import Page, mask_phone
from app.schemas.phone import (
    CancelIn,
    CompleteIn,
    ContactConsentIn,
    ContactCreateIn,
    ContactOut,
    OrderCreateIn,
    OrderDetailOut,
    OrderOut,
    OrderReviewIn,
    ScriptOut,
    ScriptPublishIn,
    ScriptReviewIn,
    TakeoverIn,
)
from app.services import phone_service as svc
from app.services import scope_service as scope_svc

router = APIRouter(prefix="/admin/phone", tags=["admin-phone"])


def _script_out(s: PhoneScript) -> ScriptOut:
    return ScriptOut(
        id=s.id, code=s.code, version=s.version, title=s.title, content=s.content,
        target_type=s.target_type, requires_recording=bool(s.requires_recording),
        status=s.status, review_note=s.review_note, created_at=s.created_at,
    )


def _contact_out(c: PhoneContact) -> ContactOut:
    return ContactOut(
        id=c.id, user_id=c.user_id, name=c.name, relation=c.relation,
        phone_masked=mask_phone(c.phone), identity_note=c.identity_note,
        consent_status=c.consent_status, consent_source=c.consent_source,
        consent_at=c.consent_at, created_at=c.created_at,
    )


def _order_out(o: PhoneCallOrder) -> OrderOut:
    return OrderOut(
        id=o.id, instance_id=o.instance_id, user_id=o.user_id, purpose=o.purpose,
        target_type=o.target_type, contact_id=o.contact_id, script_code=o.script_code,
        script_version=o.script_version, requires_recording=bool(o.requires_recording),
        status=o.status, stop_reason=o.stop_reason, attempts=o.attempts or 0,
        next_retry_at=o.next_retry_at, result=o.result, receipt_ref=o.receipt_ref,
        taken_over_by_admin_id=o.taken_over_by_admin_id, taken_over_at=o.taken_over_at,
        created_at=o.created_at,
    )


@router.get("/scripts", response_model=list[ScriptOut])
async def list_scripts(
    admin=Depends(require_permission("phone.read")),
    db: AsyncSession = Depends(get_db),
) -> list[ScriptOut]:
    rows = (await db.execute(select(PhoneScript).order_by(PhoneScript.id.desc()))).scalars().all()
    return [_script_out(s) for s in rows]


@router.post("/scripts", response_model=ScriptOut, status_code=201)
async def publish_script(
    payload: ScriptPublishIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> ScriptOut:
    script = await svc.publish_script(db, admin, payload.model_dump())
    return _script_out(script)


@router.post("/scripts/{script_id}/review", response_model=ScriptOut)
async def review_script(
    script_id: int,
    payload: ScriptReviewIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> ScriptOut:
    script = await db.get(PhoneScript, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="话术不存在")
    script = await svc.review_script(db, admin, script, payload.approve, payload.note)
    return _script_out(script)


@router.get("/contacts", response_model=Page[ContactOut])
async def list_contacts(
    user_id: int | None = Query(default=None),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("phone.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[ContactOut]:
    page, page_size = pagination
    scope = await scope_svc.load_scope(db, admin)
    scope_filter = scope_svc.user_predicate(scope, PhoneContact.user_id)
    stmt = select(PhoneContact).where(scope_filter).order_by(PhoneContact.id.desc())
    count_stmt = select(func.count()).select_from(PhoneContact).where(scope_filter)
    if user_id:
        stmt = stmt.where(PhoneContact.user_id == user_id)
        count_stmt = count_stmt.where(PhoneContact.user_id == user_id)
    total = (await db.execute(count_stmt)).scalar()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[_contact_out(c) for c in rows], total=int(total or 0), page=page, page_size=page_size)


@router.post("/contacts", response_model=ContactOut, status_code=201)
async def create_contact(
    payload: ContactCreateIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> ContactOut:
    await scope_svc.require_user_access(db, admin, payload.user_id, audit_cross_scope=False)
    contact = await svc.create_contact(db, admin, payload.model_dump())
    return _contact_out(contact)


@router.post("/contacts/{contact_id}/consent", response_model=ContactOut)
async def set_contact_consent(
    contact_id: int,
    payload: ContactConsentIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> ContactOut:
    contact = await db.get(PhoneContact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="联系人不存在")
    await scope_svc.require_user_access(db, admin, contact.user_id, audit_cross_scope=False)
    contact = await svc.set_contact_consent(
        db, admin, contact, payload.status, payload.source, payload.note
    )
    return _contact_out(contact)


@router.get("/orders", response_model=Page[OrderOut])
async def list_orders(
    instance_id: int | None = Query(default=None),
    status: str | None = Query(default=None, max_length=18),
    target_type: str | None = Query(default=None, max_length=10),
    pagination: tuple[int, int] = Depends(pagination_params),
    admin=Depends(require_permission("phone.read")),
    db: AsyncSession = Depends(get_db),
) -> Page[OrderOut]:
    page, page_size = pagination
    scope = await scope_svc.load_scope(db, admin)
    filters = [scope_svc.instance_predicate(scope, PhoneCallOrder.instance_id)]
    if instance_id:
        filters.append(PhoneCallOrder.instance_id == instance_id)
    if status:
        filters.append(PhoneCallOrder.status == status)
    if target_type:
        filters.append(PhoneCallOrder.target_type == target_type)
    count_stmt = select(func.count()).select_from(PhoneCallOrder)
    list_stmt = select(PhoneCallOrder).order_by(PhoneCallOrder.id.desc())
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar()
    rows = (await db.execute(list_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[_order_out(o) for o in rows], total=int(total or 0), page=page, page_size=page_size)


async def _get_order(
    db: AsyncSession, admin, order_id: int, *, audit_cross_scope: bool = False
) -> PhoneCallOrder:
    order = await db.get(PhoneCallOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    await scope_svc.require_instance_access(
        db, admin, order.instance_id, audit_cross_scope=audit_cross_scope
    )
    return order


@router.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(
    payload: OrderCreateIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    await scope_svc.require_instance_access(db, admin, payload.instance_id, audit_cross_scope=False)
    order = await svc.create_order(db, admin, payload.model_dump())
    return _order_out(order)


@router.get("/orders/{order_id}", response_model=OrderDetailOut)
async def order_detail(
    order_id: int,
    admin=Depends(require_permission("phone.read")),
    db: AsyncSession = Depends(get_db),
) -> OrderDetailOut:
    order = await _get_order(db, admin, order_id, audit_cross_scope=True)
    logs = (
        (
            await db.execute(
                select(PhoneCallAttempt)
                .where(PhoneCallAttempt.order_id == order.id)
                .order_by(PhoneCallAttempt.id.asc())
            )
        )
        .scalars()
        .all()
    )
    from app.schemas.phone import AttemptOut

    return OrderDetailOut(
        **_order_out(order).model_dump(),
        attempts_log=[
            AttemptOut(
                id=a.id, order_id=a.order_id, outcome=a.outcome, actor=a.actor,
                actor_admin_id=a.actor_admin_id, simulated=bool(a.simulated),
                receipt_ref=a.receipt_ref, detail=a.detail, started_at=a.started_at,
                finished_at=a.finished_at,
            )
            for a in logs
        ],
    )


@router.post("/orders/{order_id}/review", response_model=OrderOut)
async def review_order(
    order_id: int,
    payload: OrderReviewIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await svc.review_order(db, admin, await _get_order(db, admin, order_id), payload.approve, payload.note or "")
    return _order_out(order)


@router.post("/orders/{order_id}/takeover", response_model=OrderOut)
async def takeover_order(
    order_id: int,
    payload: TakeoverIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await svc.takeover_order(db, admin, await _get_order(db, admin, order_id), payload.note or "")
    return _order_out(order)


@router.post("/orders/{order_id}/complete", response_model=OrderOut)
async def complete_order(
    order_id: int,
    payload: CompleteIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await svc.complete_order_human(db, admin, await _get_order(db, admin, order_id), payload.result)
    return _order_out(order)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: int,
    payload: CancelIn,
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> OrderOut:
    order = await svc.cancel_order(db, admin, await _get_order(db, admin, order_id), payload.reason)
    return _order_out(order)


@router.post("/callback")
async def ccc_callback(
    request: Request,
    x_phone_signature: str | None = Header(default=None),
    admin=Depends(require_permission("phone.manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    body = await request.body()
    return await svc.handle_callback(db, body, x_phone_signature)
