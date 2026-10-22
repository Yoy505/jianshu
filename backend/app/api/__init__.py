"""API 路由汇总：main.py 以 /api/v1 前缀挂载。"""

from fastapi import APIRouter

from app.api.admin import audit as admin_audit
from app.api.admin import auth as admin_auth
from app.api.admin import commerce as admin_commerce
from app.api.admin import contests as admin_contests
from app.api.admin import dashboard as admin_dashboard
from app.api.admin import essays as admin_essays
from app.api.admin import mp as admin_mp
from app.api.admin import occupational_submissions as admin_occupational_submissions
from app.api.admin import outreach as admin_outreach
from app.api.admin import steward as admin_steward
from app.api.admin import volunteer_applications as admin_volunteer_applications
from app.api.admin import tasks as admin_tasks
from app.api.admin import users as admin_users
from app.api.h5 import auth as h5_auth
from app.api.h5 import occupational_submissions as h5_occupational_submissions
from app.api.h5 import volunteer_applications as h5_volunteer_applications
from app.api.mp import mp_router
from app.api.org import router as org_router
from app.api.pay import router as pay_router
from app.api.refresh import router as refresh_router
from app.api.uploads import router as uploads_router
from app.api.wx import router as wx_router

api_router = APIRouter()

# 后台
api_router.include_router(admin_auth.router)
api_router.include_router(admin_dashboard.router)
api_router.include_router(admin_contests.router)
api_router.include_router(admin_essays.router)
api_router.include_router(admin_users.router)
api_router.include_router(admin_tasks.router)
api_router.include_router(admin_audit.router)
api_router.include_router(admin_mp.router)
api_router.include_router(admin_commerce.router)
api_router.include_router(admin_occupational_submissions.router)
api_router.include_router(admin_volunteer_applications.router)
api_router.include_router(admin_steward.router)
api_router.include_router(admin_outreach.router)

# 通用 / C 端
api_router.include_router(refresh_router)
api_router.include_router(wx_router)
api_router.include_router(h5_auth.router)
api_router.include_router(h5_occupational_submissions.router)
api_router.include_router(h5_volunteer_applications.router)
api_router.include_router(uploads_router)
api_router.include_router(pay_router)

# 健康服务域（杏临汇小程序）
api_router.include_router(mp_router)

# 采购单位查看端（scope=org）
api_router.include_router(org_router)
