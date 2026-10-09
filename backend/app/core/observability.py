"""请求级可观测性：request-id 传播 + 结构化访问日志（对标 P1 #10）。

- 每个请求生成短 request-id（上游带 X-Request-ID 则透传），响应头原样返回，
  日志全链路可按 request_id 聚合；
- 完成时输出单行 JSON：{ts, event, method, path, status, duration_ms, request_id, client}；
- ContextVar 供未来业务日志挂载（audit/WS 等取当前请求 id）。
"""

import json
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

logger = logging.getLogger("app.access")


def get_request_id() -> str:
    return request_id_var.get()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        request.state.request_id = rid
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                json.dumps(
                    {
                        "event": "http",
                        "method": request.method,
                        "path": request.url.path,
                        "status": 500,
                        "duration_ms": round((time.monotonic() - start) * 1000, 1),
                        "request_id": rid,
                        "client": request.client.host if request.client else None,
                    },
                    ensure_ascii=False,
                )
            )
            request_id_var.reset(token)
            raise
        response.headers["X-Request-ID"] = rid
        logger.info(
            json.dumps(
                {
                    "event": "http",
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - start) * 1000, 1),
                    "request_id": rid,
                    "client": request.client.host if request.client else None,
                },
                ensure_ascii=False,
            )
        )
        request_id_var.reset(token)
        return response


def configure_logging() -> None:
    """应用侧日志统一 JSON 行格式（uvicorn 自身 access log 保持原样）。"""
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)s}',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger("app")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
