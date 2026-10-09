"""在 FastAPI 解析大体积 multipart 前拒绝关闭或明显超限的投稿。"""

from starlette.responses import JSONResponse

from app.core.config import settings

OCCUPATIONAL_POST_PATH = "/api/v1/h5/occupational-submissions"
MAX_REQUEST_BYTES = 320 * 1024 * 1024


class OccupationalUploadGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != OCCUPATIONAL_POST_PATH
        ):
            await self.app(scope, receive, send)
            return

        if not settings.OCCUPATIONAL_SUBMISSIONS_ENABLED:
            await JSONResponse(
                {"detail": "职业健康作品投稿暂未开放"}, status_code=503
            )(scope, receive, send)
            return

        for name, value in scope.get("headers", ()):
            if name.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await JSONResponse(
                        {"detail": "Content-Length 不正确"}, status_code=400
                    )(scope, receive, send)
                    return
                if declared > MAX_REQUEST_BYTES:
                    await JSONResponse(
                        {"detail": "请求体不能超过 320 MiB"}, status_code=413
                    )(scope, receive, send)
                    return

        # 不读取 receive；无 Content-Length 的流式请求由后续逐块文件校验处理。
        await self.app(scope, receive, send)
