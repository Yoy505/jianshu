"""杏临汇小程序（健康服务域）路由汇总：统一挂 /mp 前缀。"""

from fastapi import APIRouter

from app.api.mp import (
    appointments,
    consults,
    drugs,
    home,
    products,
    profile,
    providers,
    services,
    ws,
)

mp_router = APIRouter(prefix="/mp", tags=["mp"])
mp_router.include_router(home.router)
mp_router.include_router(services.router)
mp_router.include_router(providers.router)
mp_router.include_router(appointments.router)
mp_router.include_router(consults.router)
mp_router.include_router(drugs.router)
mp_router.include_router(profile.router)
mp_router.include_router(products.router)
# WebSocket 端点：最终路径 /api/v1/mp/ws（契约五之二）
mp_router.include_router(ws.router)
