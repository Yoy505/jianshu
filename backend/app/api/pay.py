"""微信支付服务端回调：验签 → 解密 → 入账。回调是支付成功的唯一依据之一。

响应体按微信 v3 约定：成功 {"code":"SUCCESS"}；验签失败 401、解密失败 400、
未配置 503、未知单 404、金额不符 409（微信对非 2xx 会重试，重放以幂等事件留痕）。
"""

import logging

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.services import commerce_service as svc
from app.services import wxpay_service
from app.services.audit_service import audit

logger = logging.getLogger("app.wxpay")

router = APIRouter(prefix="/pay", tags=["pay"])


@router.post("/wxpay/notify")
async def wxpay_notify(
    request: Request,
    response: Response,
    wechatpay_timestamp: str = Header(default=""),
    wechatpay_nonce: str = Header(default=""),
    wechatpay_signature: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    cfg = wxpay_service.load_config()
    body = await request.body()
    if cfg is None:
        logger.warning("wxpay notify rejected: 支付配置缺失")
        return _fail(503, "支付配置缺失")

    if not wxpay_service.verify_notify_signature(
        cfg,
        timestamp=wechatpay_timestamp,
        nonce=wechatpay_nonce,
        body=body,
        signature=wechatpay_signature,
    ):
        await audit(
            db,
            action="commerce.payment.notify_rejected",
            resource_type="payment",
            detail={"reason": "signature", "body_bytes": len(body)},
            actor_type="system",
            request=request,
        )
        await db.commit()
        return _fail(401, "签名校验失败")

    try:
        import json

        envelope = json.loads(body)
        resource = envelope.get("resource") or {}
        data = wxpay_service.decrypt_resource(cfg, resource)
    except (ValueError, KeyError, TypeError):
        await audit(
            db,
            action="commerce.payment.notify_rejected",
            resource_type="payment",
            detail={"reason": "decrypt", "body_bytes": len(body)},
            actor_type="system",
            request=request,
        )
        await db.commit()
        return _fail(400, "回调解密失败")

    out_trade_no = str(data.get("out_trade_no") or "")
    transaction_id = data.get("transaction_id")
    trade_state = str(data.get("trade_state") or "")
    amount = data.get("amount") or {}
    amount_total = amount.get("total")

    result = await svc.process_payment_result(
        db,
        out_trade_no=out_trade_no,
        transaction_id=transaction_id,
        trade_state=trade_state,
        amount_total=amount_total,
        event_type="notify_accepted",
    )

    if result in ("accepted", "replay"):
        if result == "replay":
            await audit(
                db,
                action="commerce.payment.notify_replay",
                resource_type="payment",
                detail={"out_trade_no": out_trade_no},
                actor_type="system",
                request=request,
            )
        else:
            await audit(
                db,
                action="commerce.payment.notify_accepted",
                resource_type="payment",
                detail={"out_trade_no": out_trade_no},
                actor_type="system",
                request=request,
            )
        await db.commit()
        return {"code": "SUCCESS", "message": "成功"}
    if result == "unknown_order":
        await audit(
            db,
            action="commerce.payment.notify_rejected",
            resource_type="payment",
            detail={"reason": "unknown_order", "out_trade_no": out_trade_no},
            actor_type="system",
            request=request,
        )
        await db.commit()
        return _fail(404, "订单不存在")
    if result == "amount_mismatch":
        await audit(
            db,
            action="commerce.payment.notify_rejected",
            resource_type="payment",
            detail={"reason": "amount_mismatch", "out_trade_no": out_trade_no},
            actor_type="system",
            request=request,
        )
        await db.commit()
        return _fail(409, "金额不符")
    # not_success：微信约定的终态/关闭，确认收到即可
    return {"code": "SUCCESS", "message": "成功"}


def _fail(status_code: int, message: str) -> Response:
    return Response(
        content=f'{{"code":"FAIL","message":"{message}"}}',
        status_code=status_code,
        media_type="application/json",
    )
