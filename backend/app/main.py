"""FastAPI 应用入口：CORS、路由挂载（/api/v1）、健康检查、公开上传目录。"""

import logging
import posixpath
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis import asyncio as aioredis

from app.api import api_router
from app.core.config import settings
from app.core.occupational_upload_guard import OccupationalUploadGuard
from app.core.observability import RequestLoggingMiddleware, configure_logging
from app.core.storage_paths import configured_storage_path
from app.services.export_storage import private_export_dir

logger = logging.getLogger("app")

# 参数校验错误信息映射为中文（FastAPI 默认 422 的 msg 为英文）
_VALIDATION_MSG_MAP = {
    "missing": "该字段为必填项",
    "enum": "取值不在允许范围内",
    "int_parsing": "应为整数",
    "bool_parsing": "应为布尔值",
    "string_type": "应为字符串",
    "string_too_short": "长度不足",
    "string_too_long": "长度超限",
    "greater_than_equal": "数值过小",
    "less_than_equal": "数值过大",
    "datetime_from_datetime_parsing": "时间格式不正确",
    "json_invalid": "JSON 格式不正确",
    "mapping_type": "请求体应为 JSON 对象",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 公开上传和私有导出目录必须分开。
    configured_storage_path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    private_export_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    # Redis 连接容错：不可用仅告警，不阻断启动
    try:
        client = aioredis.from_url(settings.REDIS_URL)
        await client.ping()
        await client.aclose()
    except Exception as exc:
        logger.warning("Redis 连接失败（%s），缓存与短信功能将不可用", exc)
    yield


# 生产/预生产关闭交互式文档与 OpenAPI schema（D4 加固；dev/test 保留便于联调）
_PUBLIC_ENV = settings.APP_ENV not in {"dev", "test"}

app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs" if not _PUBLIC_ENV else None,
    redoc_url="/redoc" if not _PUBLIC_ENV else None,
    openapi_url="/openapi.json" if not _PUBLIC_ENV else None,
)

configure_logging()
app.add_middleware(OccupationalUploadGuard)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = ".".join(str(part) for part in first.get("loc", []) if part not in ("body", "query", "path"))
        msg = _VALIDATION_MSG_MAP.get(first.get("type", ""), str(first.get("msg", "参数错误")))
        detail = f"参数校验失败：{loc} {msg}".strip()
    else:
        detail = "参数校验失败"
    return JSONResponse(status_code=422, content={"detail": detail})


class PublicUploads(StaticFiles):
    """拒绝访问旧版公开导出目录，避免历史文件继续裸露。"""

    async def get_response(self, path: str, scope: dict):
        normalized = posixpath.normpath("/" + path.replace("\\", "/")).lstrip("/")
        if normalized == "exports" or normalized.startswith("exports/"):
            raise HTTPException(status_code=404, detail="文件不存在")
        return await super().get_response(path, scope)


# 仅普通上传文件保留静态访问；/uploads/exports 始终被拒绝。
app.mount(
    "/uploads",
    PublicUploads(directory=configured_storage_path(settings.UPLOAD_DIR), check_dir=False),
    name="uploads",
)
