"""中间件配置"""
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from json import dumps

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config.settings import settings

logger = logging.getLogger(__name__)


class RequestBodyTooLargeError(Exception):
    """请求实际读取字节数超过限制。"""


class RequestBodyLimitMiddleware:
    """在 multipart 解析前限制资产上传的声明及实际请求体大小。"""

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int,
        json_max_body_bytes: int | None = None,
        forgecad_import_max_body_bytes: int | None = None,
    ) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.json_max_body_bytes = json_max_body_bytes or max_body_bytes
        self.forgecad_import_max_body_bytes = (
            forgecad_import_max_body_bytes or max_body_bytes
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        if (
            method == "POST"
            and path == f"{settings.API_V1_PREFIX}/assets/upload"
        ):
            active_limit = self.max_body_bytes
        elif (
            method == "POST"
            and path == f"{settings.API_V1_PREFIX}/forgecad/import"
        ):
            active_limit = self.forgecad_import_max_body_bytes
        elif (
            method in {"POST", "PUT", "PATCH"}
            and path.startswith(f"{settings.API_V1_PREFIX}/")
        ):
            active_limit = self.json_max_body_bytes
        else:
            await self.app(scope, receive, send)
            return

        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        raw_content_length = headers.get(b"content-length")
        if raw_content_length is not None:
            try:
                content_length = int(raw_content_length)
            except ValueError:
                await self._send_error(send, 400, "Content-Length 无效")
                return
            if content_length < 0:
                await self._send_error(send, 400, "Content-Length 无效")
                return
            if content_length > active_limit:
                await self._send_error(send, 413, "请求体超过大小限制")
                return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                received_bytes += len(body)
                if received_bytes > active_limit:
                    raise RequestBodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLargeError:
            await self._send_error(send, 413, "请求体超过大小限制")

    @staticmethod
    async def _send_error(send: Send, status_code: int, detail: str) -> None:
        content = dumps(
            {"detail": detail},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(content)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": content})


def setup_middleware(app: FastAPI) -> None:
    """配置应用中间件"""
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=(
            settings.UPLOAD_MAX_SIZE + settings.ASSET_UPLOAD_OVERHEAD_MAX_BYTES
        ),
        json_max_body_bytes=settings.JSON_REQUEST_MAX_BYTES,
        forgecad_import_max_body_bytes=(
            settings.FORGECAD_IMPORT_MAX_BYTES
            + settings.ASSET_UPLOAD_OVERHEAD_MAX_BYTES
        ),
    )
    # CORS
    cors_kwargs = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "max_age": 600,
    }
    if "*" in settings.ALLOWED_ORIGINS:
        logger.error("[安全] credentialed CORS 禁止通配符，已拒绝全部跨域来源。")
        cors_kwargs["allow_origins"] = []
    else:
        cors_kwargs["allow_origins"] = settings.ALLOWED_ORIGINS

    app.add_middleware(CORSMiddleware, **cors_kwargs)

    # 请求日志
    @app.middleware("http")
    async def request_logger(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start_time = time.perf_counter()
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        logger.info(
            "[request.start] request_id=%s method=%s path=%s",
            request_id, request.method, request.url.path,
        )

        try:
            origin = request.headers.get("Origin")
            if (
                origin is not None
                and request.url.path.startswith(settings.API_V1_PREFIX)
                and request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and origin not in settings.ALLOWED_ORIGINS
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "请求来源不受信任"},
                )
            response = await call_next(request)
            return response
        except Exception:
            logger.exception("[request.error] request_id=%s", request_id)
            raise
        finally:
            process_time = time.perf_counter() - start_time
            logger.info(
                "[request.end] request_id=%s status=%s cost_ms=%.2f",
                request_id, "-", process_time * 1000,
            )
