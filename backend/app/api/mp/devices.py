"""C 端 · 设备数据接口（工作二切片 C，契约 docs/api.md 第五部分 · 八）。

仅本人数据；趋势只含 valid/reviewed，无数据日不填补；不承诺医学判断。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models import DeviceBinding, User
from app.schemas.device import BindingOut, MyDeviceOut, TrendOut, UnbindIn
from app.services import device_service as svc
from app.services import steward_service

router = APIRouter(prefix="/steward/devices", tags=["mp-devices"])


async def _my_binding(db, user: User) -> DeviceBinding | None:
    return await svc.active_binding_for_user(db, user.id)


@router.get("", response_model=MyDeviceOut)
async def my_device(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MyDeviceOut:
    binding = await _my_binding(db, user)
    consent_granted = await svc._device_consent_ok(db, user.id)
    binding_out = None
    if binding is not None:
        binding_out = BindingOut(
            id=binding.id,
            user_id=binding.user_id,
            vendor_id=binding.vendor_id,
            device_model=binding.device_model,
            device_sn=binding.device_sn[-4:].rjust(len(binding.device_sn), "*"),  # 序列号掩码
            status=binding.status,
            bound_at=binding.bound_at,
            unbound_at=binding.unbound_at,
            unbind_reason=binding.unbind_reason,
        )
    sync_note = (
        "厂商适配待配置：设备型号与厂商确定后开始自动同步"
        if binding is None or not consent_granted
        else "已具备同步条件；最近同步时间见趋势页"
    )
    return MyDeviceOut(
        binding=binding_out,
        consent_granted=consent_granted,
        consent_outdated=False,
        sync_note=sync_note,
    )


@router.get("/trend", response_model=TrendOut)
async def my_trend(
    metric: str = Query(default="sleep_duration", max_length=20),
    days: int = Query(default=14, ge=1, le=90),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TrendOut:
    from app.models import DEVICE_METRIC_RANGES

    if metric not in DEVICE_METRIC_RANGES:
        raise HTTPException(status_code=422, detail="未知的指标类型")
    data = await svc.user_trend(db, user.id, metric, days)
    return TrendOut(**data)


@router.post("/bindings/{binding_id}/unbind")
async def unbind_my_device(
    binding_id: int,
    payload: UnbindIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    binding = await db.get(DeviceBinding, binding_id)
    if binding is None or binding.user_id != user.id:
        raise HTTPException(status_code=404, detail="绑定不存在")
    await svc.unbind(db, user, binding, payload.reason, by_user=True)
    return {"id": binding.id, "status": "unbound"}
