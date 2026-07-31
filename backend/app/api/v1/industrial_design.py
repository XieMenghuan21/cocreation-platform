"""工业品设计统一工作流接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.core.identity import auth_user_id
from app.db.session import get_db
from app.schemas.industrial_design import IndustrialDesignWorkflowRequest
from app.services.cad_ai_gateway_service import CadAiGatewayError
from app.services.design_review_service import (
    DesignReviewServiceError,
    design_review_service,
)
from app.services.engineering_package_service import (
    EngineeringPackageServiceError,
    engineering_package_service,
)
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


@router.post("/workflows/{task_id}/engineering-package", response_model=dict, summary="导出工程设计包")
def create_engineering_package(
    task_id: str,
    db: Session = Depends(get_db),
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """把任务产物打包为可交付的工程设计包（zip：设计说明 + 图纸 + 数模 + BOM）。"""
    user_id = auth_user_id(auth_user)
    try:
        result = engineering_package_service.build_package(
            db=db,
            user_id=user_id,
            task_id=task_id,
            publish_assets=True,
        )
        db.commit()
        return success_response(data=result, message="工程设计包已生成")
    except EngineeringPackageServiceError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message,
                code=exc.status_code,
                error_code=exc.error_code,
            ),
        )


@router.post("/workflows/{task_id}/design-review", response_model=dict, summary="生成设计审查报告")
async def create_design_review(
    task_id: str,
    db: Session = Depends(get_db),
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """对任务 3D 模型执行几何规则检查并生成设计审查报告（PDF）。"""
    user_id = auth_user_id(auth_user)
    try:
        result = await design_review_service.create_review(
            db=db,
            user_id=user_id,
            task_id=task_id,
            publish_assets=True,
        )
        db.commit()
        return success_response(data=result, message="设计审查报告已生成")
    except DesignReviewServiceError as exc:
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
