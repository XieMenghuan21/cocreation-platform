"""统一响应格式工具"""
from typing import Any, Optional
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel


class ApiResponse(BaseModel):
    """统一 API 响应格式"""
    code: int = 200
    message: str = "success"
    data: Optional[Any] = None
    success: bool = True
    errorCode: Optional[str] = None
    requestId: Optional[str] = None


def success_response(
    data: Any = None,
    message: str = "success",
    code: int = 200,
    request_id: Optional[str] = None,
) -> dict:
    return {
        "code": code,
        "message": message,
        "data": jsonable_encoder(data),
        "success": True,
        "errorCode": None,
        "requestId": request_id,
    }


def error_response(
    message: str,
    code: int = 400,
    data: Any = None,
    error_code: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    return {
        "code": code,
        "message": message,
        "data": jsonable_encoder(data),
        "success": False,
        "errorCode": error_code,
        "requestId": request_id,
    }
