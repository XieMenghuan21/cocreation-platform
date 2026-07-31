"""自定义异常处理"""
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.utils.response import error_response

logger = logging.getLogger(__name__)


class APIException(HTTPException):
    """API 基础异常"""
    def __init__(
        self,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        message: str = "请求失败",
        detail: str = None,
        error_code: str | None = None,
        data: Any = None,
    ):
        super().__init__(status_code=status_code, detail=detail or message)
        self.message = detail or message
        self.error_code = error_code
        self.data = data


class NotFoundException(APIException):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, message=message, error_code="NOT_FOUND")


class UnauthorizedException(APIException):
    def __init__(self, message: str = "未授权访问"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, message=message, error_code="UNAUTHORIZED")


def setup_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器"""

    @app.exception_handler(APIException)
    async def handle_api_exception(request: Request, exc: APIException):
        request_id = getattr(request.state, "request_id", "-")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message, code=exc.status_code,
                data=exc.data, error_code=exc.error_code, request_id=request_id,
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException):
        request_id = getattr(request.state, "request_id", "-")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=str(exc.detail) if exc.detail else "请求失败",
                code=exc.status_code, error_code=f"HTTP_{exc.status_code}",
                request_id=request_id,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", "-")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_response(
                message="请求参数校验失败", code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={"errors": exc.errors()}, error_code="REQUEST_VALIDATION_ERROR",
                request_id=request_id,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "-")
        logger.exception("[unexpected.error] request_id=%s message=%s", request_id, str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response(
                message="服务器内部异常，请稍后重试",
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="INTERNAL_SERVER_ERROR", request_id=request_id,
            ),
        )
