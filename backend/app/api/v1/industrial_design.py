"""工业品设计统一工作流接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import require_auth
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest
from app.services.cad_ai_gateway_service import CadAiGatewayError
from app.services.industrial_design_workflow_service import industrial_design_workflow_service
from app.utils.response import error_response, success_response

router = APIRouter(prefix="/industrial-design")


@router.post("/workflows", response_model=dict, summary="创建工业品设计统一工作流")
async def create_industrial_design_workflow(
    request: IndustrialDesignWorkflowRequest,
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """统一承接语言描述、语音描述、图纸上传和 CAD 二创输入。"""
    try:
        result = await industrial_design_workflow_service.create_workflow(request, auth_user=auth_user)
        return success_response(data=result, message="工业品设计工作流已创建")
    except CadAiGatewayError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message,
                code=exc.status_code,
                error_code=exc.error_code,
            ),
        )


@router.get("/workflows/{task_id}", response_model=dict, summary="查询工业品设计统一工作流")
async def get_industrial_design_workflow(
    task_id: str,
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """查询工业品设计任务状态。"""
    try:
        result = await industrial_design_workflow_service.get_workflow(task_id, auth_user=auth_user)
        return success_response(data=result, message="工业品设计工作流状态已返回")
    except CadAiGatewayError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message,
                code=exc.status_code,
                error_code=exc.error_code,
            ),
        )


@router.get("/assets/image_edits/{filename}", summary="下载工业品设计图片精修资产")
def download_industrial_design_image_edit_asset(
    filename: str,
    auth_user: dict[str, object] = Depends(require_auth),
) -> None:
    """旧图片精修文件路径已停用，统一使用数据库资产下载。"""
    del filename, auth_user
    raise HTTPException(
        status_code=410,
        detail="旧图片精修端点已停用，请使用 /api/v1/assets/{id}/download",
    )


@router.get("/assets/{filename}", summary="下载工业品设计生成资产")
def download_industrial_design_asset(
    filename: str,
    auth_user: dict[str, object] = Depends(require_auth),
) -> None:
    """旧工业设计文件路径已停用，统一使用数据库资产下载。"""
    del filename, auth_user
    raise HTTPException(
        status_code=410,
        detail="旧工业设计端点已停用，请使用 /api/v1/assets/{id}/download",
    )
