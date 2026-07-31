"""ForgeCAD AI 建模接口。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_auth
from app.config.settings import settings
from app.types.json import JSONValue
from app.core.identity import auth_user_id
from app.db.session import get_db
from app.schemas.forgecad import ForgeCadGenerateRequest, ForgeCadImportAsset
from app.schemas.furniture_drawing import WardrobeDrawingRequest
from app.services.ai_model_gateway_service import ai_model_gateway_service
from app.services.cad_ai_gateway_service import CadAiGatewayError, cad_ai_gateway_service
from app.services.forgecad_service import ForgeCadServiceError, forgecad_service
from app.services.furniture_drawing_service import furniture_drawing_service
from app.utils.response import error_response, success_response

router = APIRouter(prefix="/forgecad")
logger = logging.getLogger(__name__)


class GenerateWithDrawingRequest(BaseModel):
    """建模 + 出图联合请求。"""

    forgecad_request: ForgeCadGenerateRequest = Field(alias="forgecadRequest")
    drawing_request: WardrobeDrawingRequest = Field(alias="drawingRequest")


class CadAiAutoGenerateRequest(BaseModel):
    """CAD AI 自动生成工作流请求。"""

    input_type: str = Field(alias="inputType", pattern="^(text|voice|cad|image|pdf)$")
    text: str | None = Field(default=None, max_length=12000)
    asset_ids: list[str] = Field(default_factory=list, alias="assetIds", max_length=20)
    asset_urls: list[str] = Field(default_factory=list, alias="assetUrls", max_length=20)
    asset_metas: list[dict[str, object]] = Field(default_factory=list, alias="assetMetas", max_length=20)
    options: dict[str, object] = Field(default_factory=dict)
    project_name: str | None = Field(default=None, alias="projectName", max_length=120)
    industry: str | None = Field(default=None, max_length=80)


def _cad_ai_error_response(exc: CadAiGatewayError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            message=exc.message,
            code=exc.status_code,
            error_code=exc.error_code,
        ),
    )


def _remote_cad_persistence_unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=error_response(
            message=(
                "远端 CAD AI 任务接口已禁用：当前远端协议不能保证将全部结果文件"
                "回收入 PostgreSQL。请使用数据库资产化的工业设计工作流。"
            ),
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="CAD_AI_DATABASE_ASSET_RECOVERY_UNAVAILABLE",
        ),
    )


@router.get("/cad-ai/health", response_model=dict, summary="检查 CAD AI 统一网关状态")
async def check_cad_ai_gateway_health(
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """代理检查远端同端口 `/cad-ai` 服务是否可用。"""
    _ = auth_user
    try:
        result = await cad_ai_gateway_service.health()
        return success_response(data=result, message="CAD AI 网关可用")
    except CadAiGatewayError as exc:
        return _cad_ai_error_response(exc)


@router.post("/auto-generate", response_model=dict, summary="提交 CAD AI 自动生成任务")
async def auto_generate_cad_project(
    request: CadAiAutoGenerateRequest,
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """提交文字/语音/上传文件到工程图、3D、爆炸图的自动编排任务。"""
    del request, auth_user
    return _remote_cad_persistence_unavailable_response()


@router.get("/tasks/{task_id}", response_model=dict, summary="查询 CAD AI 自动生成任务")
async def get_cad_ai_task(
    task_id: str,
    auth_user: dict[str, object] = Depends(require_auth),
) -> dict[str, object] | JSONResponse:
    """代理查询远端 CAD AI 任务状态。"""
    del task_id, auth_user
    return _remote_cad_persistence_unavailable_response()


@router.get("/assets/{asset_id}/download", summary="下载 CAD AI 生成资产")
async def download_cad_ai_asset(
    asset_id: str,
    auth_user: dict[str, object] = Depends(require_auth),
):
    """远端资产代理已停用，统一使用已入库资产下载。"""
    del asset_id, auth_user
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="远端资产代理已停用，请使用 /api/v1/assets/{id}/download",
    )


@router.post("/import", response_model=dict, summary="导入 CAD 或图纸参考文件")
async def import_forgecad_asset(
    file: UploadFile = File(...),
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object] | JSONResponse:
    """保存用户上传的 CAD/图纸文件，作为后续 AI 生成设计方案的真实输入。"""
    try:
        content_buffer = bytearray()
        while True:
            chunk = await file.read(settings.ASSET_CHUNK_SIZE_BYTES)
            if not chunk:
                break
            if len(content_buffer) + len(chunk) > forgecad_service.max_import_size_bytes:
                raise ForgeCadServiceError(
                    "导入文件超过 50MB，请压缩或拆分后再上传",
                    "FORGECAD_IMPORT_TOO_LARGE",
                    status_code=413,
                )
            content_buffer.extend(chunk)
        def save_and_commit() -> ForgeCadImportAsset:
            result = forgecad_service.save_import_asset(
                db=db,
                user_id=auth_user_id(auth_user),
                filename=file.filename or "",
                content_type=file.content_type or "application/octet-stream",
                content=bytes(content_buffer),
            )
            db.commit()
            return result

        result = await run_in_threadpool(save_and_commit)
        return success_response(
            data=result.model_dump(by_alias=True),
            message="CAD 图纸导入成功",
        )
    except ForgeCadServiceError as exc:
        await run_in_threadpool(db.rollback)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message,
                code=exc.status_code,
                error_code=exc.error_code,
                data=exc.detail.model_dump(by_alias=True) if exc.detail else None,
            ),
        )
    except Exception as exc:
        await run_in_threadpool(db.rollback)
        logger.exception("CAD 图纸导入失败")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response(
                message="CAD 图纸导入失败，请稍后重试",
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="FORGECAD_IMPORT_FAILED",
            ),
        )
    finally:
        await file.close()


@router.post("/voice/import", response_model=dict, summary="导入语音设计描述")
async def import_forgecad_voice_asset(
    file: UploadFile = File(...),
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object] | JSONResponse:
    """保存用户语音描述文件，供远端 CAD AI 自动工作流转写并生成设计。"""
    return await import_forgecad_asset(file=file, auth_user=auth_user, db=db)


@router.post("/generate", response_model=dict, summary="生成 ForgeCAD 参数化建模脚本")
async def generate_forgecad_model(
    request: ForgeCadGenerateRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object] | JSONResponse:
    """调用 Qwen3 生成 ForgeCAD 脚本，并按需执行 ForgeCAD CLI。"""
    try:
        result = await ai_model_gateway_service.generate_cad(
            request,
            db=db,
            user_id=auth_user_id(auth_user),
        )
        db.commit()
        return success_response(
            data=result.model_dump(by_alias=True),
            message="ForgeCAD 建模任务生成成功",
        )
    except ForgeCadServiceError as exc:
        db.rollback()
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message,
                code=exc.status_code,
                error_code=exc.error_code,
                data=exc.detail.model_dump(by_alias=True) if exc.detail else None,
            ),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("ForgeCAD 建模任务生成失败")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response(
                message="ForgeCAD 建模任务生成失败，请稍后重试",
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="FORGECAD_GENERATE_FAILED",
            ),
        )


@router.get("/download/{task_id}", summary="下载 ForgeCAD 生成的输出文件")
async def download_forgecad_file(
    task_id: str,
    auth_user: dict[str, object] = Depends(require_auth),
):
    """旧路径不再读取业务磁盘；客户端应使用统一资产下载 API。"""
    del task_id, auth_user
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="旧 ForgeCAD 下载端点已停用，请使用 /api/v1/assets/{id}/download",
    )


@router.get("/import/{asset_id}/file", summary="预览或下载导入的 CAD 文件")
async def download_imported_cad_file(
    asset_id: str,
    auth_user: dict[str, object] = Depends(require_auth),
):
    """旧导入文件路径已停用，统一使用数据库资产下载。"""
    del asset_id, auth_user
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="旧导入文件端点已停用，请使用 /api/v1/assets/{id}/download",
    )


@router.get("/import/{asset_id}/preview-file", summary="预览 STEP 转换后的 3D 文件")
async def download_imported_cad_preview_file(
    asset_id: str,
    auth_user: dict[str, object] = Depends(require_auth),
):
    """旧预览文件路径已停用，统一使用数据库资产下载。"""
    del asset_id, auth_user
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="旧预览文件端点已停用，请使用 /api/v1/assets/{id}/download",
    )


@router.post("/generate-with-drawing", response_model=dict, summary="建模 + 出图联合生成")
async def generate_with_drawing(
    request: GenerateWithDrawingRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object] | JSONResponse:
    """一次调用同时完成 ForgeCAD 建模和工程图出图。"""
    user_id = auth_user_id(auth_user)
    forgecad_result = None
    forgecad_error: str | None = None
    try:
        forgecad_result = await ai_model_gateway_service.generate_cad(
            request.forgecad_request,
            db=db,
            user_id=user_id,
        )
    except ForgeCadServiceError as exc:
        forgecad_error = exc.message
    except Exception as exc:
        logger.exception("联合生成中的 ForgeCAD 建模失败")
        forgecad_error = "ForgeCAD 建模失败"

    drawing_result = None
    drawing_error: str | None = None
    try:
        drawing_result = furniture_drawing_service.render_and_store(
            db=db,
            user_id=user_id,
            request=request.drawing_request,
        )
    except ValueError as exc:
        drawing_error = str(exc)
    except Exception as exc:
        logger.exception("联合生成中的工程图生成失败")
        drawing_error = "工程图生成失败"

    if forgecad_error and drawing_error:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response(
                message=f"建模和出图均失败：{forgecad_error}；{drawing_error}",
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code="GENERATE_WITH_DRAWING_BOTH_FAILED",
            ),
        )

    data: dict[str, JSONValue] = {}
    if forgecad_result:
        data["forgecadResult"] = forgecad_result.model_dump(by_alias=True)
    if forgecad_error:
        data["forgecadError"] = forgecad_error
    if drawing_result:
        data["drawingResult"] = drawing_result.model_dump(by_alias=True)
    if drawing_error:
        data["drawingError"] = drawing_error

    db.commit()
    return success_response(
        data=data,
        message="建模与出图联合任务完成" if forgecad_result and drawing_result else "部分任务完成",
    )
