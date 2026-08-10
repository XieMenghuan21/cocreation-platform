"""工业品设计统一工作流服务。"""
from __future__ import annotations

import asyncio
import logging
import re
import httpx
from threading import Lock
from pathlib import Path
import uuid
from base64 import b64decode
from binascii import Error as Base64DecodeError
from contextlib import nullcontext
from datetime import datetime, timezone
from copy import deepcopy
from collections.abc import Callable, Coroutine
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config.settings import settings
from app.types.json import JSONValue
from app.core.identity import auth_user_id
from app.db.session import get_db_context
from app.schemas.cocreation_history import ProjectRecordPayload, VersionSnapshotPayload
from app.schemas.forgecad import ForgeCadGenerateRequest
from app.schemas.furniture_drawing import WardrobeDrawingRequest
from app.schemas.industrial_design import (
    IndustrialDesignAssetMeta,
    IndustrialDesignImageEditRequest,
    IndustrialDesignWorkflowRequest,
)
from app.models.persistence import Asset
from app.services.ai_model_gateway_service import ai_model_gateway_service
from app.services.asset_blob_service import AssetBlobService
from app.services.cocreation_history_service import cocreation_history_service
from app.services.dashscope_image_service import DashScopeImageServiceError
from app.services.cad_ai_gateway_service import CadAiGatewayError, cad_ai_gateway_service
from app.services.cad_build123d_service import (
    Build123dServiceError,
    build123d_service,
)
from app.services.forgecad_service import ForgeCadServiceError, forgecad_service
from app.services.furniture_drawing_service import furniture_drawing_service
from app.services.image2_edit_service import Image2EditServiceError, image2_edit_service
from app.services.nodapi_image_service import NodApiImageServiceError
from app.services.gemini_image_service import GeminiImageServiceError
from app.services.nodapi_midjourney_service import (
    NodApiMidjourneyServiceError,
    nodapi_midjourney_service,
)
from app.services.safe_content_validator import (
    is_valid_image,
    trusted_image_decoder_available,
)
from app.services.zoo_design_service import ZooDesignServiceError, zoo_design_service
from app.services.workflow_task_repository import WorkflowTaskRepository

logger = logging.getLogger(__name__)
_HISTORY_LOCKS = tuple(Lock() for _ in range(64))


class IndustrialDesignWorkflowService:
    """把文字、语音、图纸和 CAD 输入收敛成同一条工业品设计工作流。"""

    max_generated_asset_size_bytes = 50 * 1024 * 1024

    def __init__(
        self,
        *,
        cad_ai_gateway=cad_ai_gateway_service,
        forgecad_service=forgecad_service,
        drawing_service=furniture_drawing_service,
        ai_model_gateway=ai_model_gateway_service,
        build123d_service=build123d_service,
        zoo_design_service=zoo_design_service,
        image2_edit_service=image2_edit_service,
        nodapi_midjourney_service=nodapi_midjourney_service,
        history_service=cocreation_history_service,
        db_context_factory=get_db_context,
        repository_factory: Callable[[Session], WorkflowTaskRepository] = WorkflowTaskRepository,
        task_scheduler: Callable[[Coroutine[object, object, None]], object] = asyncio.create_task,
        worker_id: str | None = None,
        lease_seconds: float = 120,
        asset_service: AssetBlobService | None = None,
        runtime_temp_root: Path | None = None,
        trusted_image_validator: Callable[[str, bytes], bool] | None = None,
    ) -> None:
        self.cad_ai_gateway = cad_ai_gateway
        self.forgecad_service = forgecad_service
        self.drawing_service = drawing_service
        self.ai_model_gateway = ai_model_gateway
        self.build123d_service = build123d_service
        self.zoo_design_service = zoo_design_service
        self.image2_edit_service = image2_edit_service
        self.nodapi_midjourney_service = nodapi_midjourney_service
        self.history_service = history_service
        self.db_context_factory = db_context_factory
        self.repository_factory = repository_factory
        self.task_scheduler = task_scheduler
        self.worker_id = worker_id or f"industrial-worker-{uuid.uuid4().hex}"
        self.lease_seconds = lease_seconds
        self.asset_service = asset_service or AssetBlobService(
            chunk_size=settings.ASSET_CHUNK_SIZE_BYTES
        )
        self.runtime_temp_root = runtime_temp_root
        self.trusted_image_validator = trusted_image_validator or (
            is_valid_image if trusted_image_decoder_available() else None
        )
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _schedule(self, coroutine: Coroutine[object, object, None]) -> None:
        scheduled = self.task_scheduler(coroutine)
        if not isinstance(scheduled, asyncio.Task):
            return
        self._background_tasks.add(scheduled)
        scheduled.add_done_callback(self._background_task_done)

    def _background_task_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            exception = task.exception()
        except asyncio.CancelledError:
            return
        if exception is not None:
            logger.error(
                "后台工作流任务异常",
                exc_info=(type(exception), exception, exception.__traceback__),
            )

    async def shutdown(self) -> None:
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def create_workflow(
        self,
        request: IndustrialDesignWorkflowRequest,
        auth_user: dict[str, object] | None = None,
    ) -> dict[str, JSONValue]:
        """创建统一工业品设计任务；有远端编排服务时优先转发，没有时本地生成可用结果。"""
        user_id = self._resolve_user_id(auth_user)
        request = await run_in_threadpool(
            self._validate_input_assets_sync,
            request,
            user_id,
        )
        if self._workflow_uses_external_images(request):
            self._ensure_trusted_image_decoder()
        if self._remote_gateway_configured():
            result = await self._create_remote_workflow(request, auth_user=auth_user)
            await self._persist_created_task(request, result, auth_user)
            await self._persist_terminal_history_if_needed(result, request, auth_user)
            return result
        if self._external_chain_configured(request):
            return await self._enqueue_external_workflow(request, auth_user=auth_user)
        with self.db_context_factory() as db:
            workflow_id = f"industrial_design_{uuid.uuid4().hex[:16]}"
            project_name = request.project_name or self._default_project_name(request)
            design_spec = self._build_design_spec(request)
            now = datetime.now(timezone.utc).isoformat()
            pending_task: dict[str, JSONValue] = {
                "taskId": workflow_id,
                "status": "pending",
                "progress": 5,
                "currentStep": "工业品设计任务正在本地生成。",
                "sourceMode": request.input_type,
                "projectId": project_name,
                "versionId": f"v_{workflow_id[-8:]}",
                "designSpec": design_spec,
                "outputs": {},
                "diagnostics": [],
                "createdAt": now,
                "updatedAt": now,
            }
            repository = self.repository_factory(db)
            repository.create(user_id, request, pending_task)
            result = await self._create_local_workflow(
                request,
                user_id=user_id,
                db=db,
                workflow_id=workflow_id,
            )
            self._finalize_staged_output_assets(
                db,
                repository=repository,
                workflow_id=str(result["taskId"]),
                outputs=cast(dict[str, object], result.get("outputs") or {}),
                publish=result.get("status") == "completed",
            )
            repository.update_and_append_event(
                workflow_id,
                status=str(result["status"]),
                progress=int(result["progress"]),
                current_step=str(result["currentStep"]),
                design_spec=cast(dict[str, object], result["designSpec"]),
                outputs=cast(dict[str, object], result["outputs"]),
                diagnostics=cast(list[dict[str, object]], result["diagnostics"]),
                error_code=(
                    str(result["errorCode"])
                    if result.get("errorCode") is not None
                    else None
                ),
                error_message=(
                    str(result["errorMessage"])
                    if result.get("errorMessage") is not None
                    else None
                ),
                recoverable=bool(result["recoverable"]),
                event_type=(
                    "completed"
                    if result["status"] == "completed"
                    else "failed"
                ),
                message=str(result["currentStep"]),
            )
        await self._persist_terminal_history_if_needed(result, request, auth_user)
        return result

    def _validate_input_assets_sync(
        self,
        request: IndustrialDesignWorkflowRequest,
        user_id: str,
    ) -> IndustrialDesignWorkflowRequest:
        raw_ids = [
            *request.asset_ids,
            *[item.asset_id for item in request.asset_metas],
        ]
        if request.asset_urls and not raw_ids:
            raise CadAiGatewayError(
                "输入资产必须先上传到数据库资产库",
                "WORKFLOW_INPUT_ASSET_UNAVAILABLE",
                status_code=422,
            )
        ordered_ids = list(dict.fromkeys(raw_ids))
        if not ordered_ids:
            return request.model_copy(update={"asset_urls": []})
        try:
            parsed_ids = [UUID(asset_id) for asset_id in ordered_ids]
        except ValueError as exc:
            raise CadAiGatewayError(
                "输入资产标识无效",
                "WORKFLOW_INPUT_ASSET_UNAVAILABLE",
                status_code=422,
            ) from exc
        with self.db_context_factory() as db:
            assets = list(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(parsed_ids),
                        Asset.user_id == user_id,
                        Asset.status == "available",
                    )
                )
            )
        assets_by_id = {str(asset.id): asset for asset in assets}
        if any(asset_id not in assets_by_id for asset_id in ordered_ids):
            raise CadAiGatewayError(
                "输入资产不存在、不可用或不属于当前用户",
                "WORKFLOW_INPUT_ASSET_UNAVAILABLE",
                status_code=404,
            )
        validated_metas = [
            IndustrialDesignAssetMeta(
                assetId=asset_id,
                filename=assets_by_id[asset_id].filename,
                extension=assets_by_id[asset_id].extension or "",
                contentType=assets_by_id[asset_id].content_type,
                sizeBytes=assets_by_id[asset_id].size_bytes,
                parseStatus="stored",
                parseMessage="数据库资产已验证",
                previewAssetUrl=self._asset_url(str(asset_id)),
            )
            for asset_id in ordered_ids
        ]
        return request.model_copy(
            update={
                "asset_ids": ordered_ids,
                "asset_urls": [],
                "asset_metas": validated_metas,
            }
        )

    async def get_workflow(
        self,
        task_id: str,
        auth_user: dict[str, object] | None = None,
    ) -> dict[str, JSONValue]:
        """读取工业品设计任务状态。"""
        user_id = self._resolve_user_id(auth_user)
        task = await run_in_threadpool(self._get_task_sync, task_id, user_id)
        if task is not None:
            return task
        raise CadAiGatewayError(
            "工业品设计任务不存在",
            "INDUSTRIAL_DESIGN_TASK_NOT_FOUND",
            status_code=404,
        )

    def _remote_gateway_configured(self) -> bool:
        return bool(str(getattr(self.cad_ai_gateway, "base_url", "") or "").strip())

    def _cad_provider(self, request: IndustrialDesignWorkflowRequest) -> str:
        requested = (request.options.cad_provider or "").strip().lower()
        if requested in {"build123d", "forgecad"}:
            return requested
        env_provider = str(getattr(settings, "CAD_PROVIDER", "") or "").strip().lower()
        if env_provider in {"build123d", "forgecad"}:
            return env_provider
        if not self.forgecad_service.bridge_base_url and self.build123d_service.available:
            return "build123d"
        return "forgecad"

    def _external_chain_configured(self, request: IndustrialDesignWorkflowRequest) -> bool:
        if request.options.generate_cad or request.options.generate_plan_line:
            return True
        if request.options.generate_render_views:
            return self.build123d_service.available
        if request.options.generate_drawing and self.ai_model_gateway.image_configured():
            return True
        if request.options.generate_render and self.ai_model_gateway.image_configured():
            return True
        if request.options.generate_explosion and self.ai_model_gateway.image_configured():
            return True
        return False

    def _workflow_uses_external_images(
        self,
        request: IndustrialDesignWorkflowRequest,
    ) -> bool:
        generates_external_image = bool(
            self.ai_model_gateway.image_configured()
            and (
                request.options.generate_drawing
                or request.options.generate_render
                or request.options.generate_explosion
            )
        )
        enhances_uploaded_image = bool(
            request.options.enhance_image
            and (request.options.generate_render or request.options.generate_explosion)
            and self._resolve_first_uploaded_image_asset_id(request) is not None
        )
        return generates_external_image or enhances_uploaded_image

    def _ensure_trusted_image_decoder(self) -> None:
        if self.trusted_image_validator is None:
            raise CadAiGatewayError(
                "图片处理能力不可用：当前环境缺少受信图片解码器",
                "IMAGE_PIPELINE_TRUSTED_DECODER_UNAVAILABLE",
                status_code=503,
            )

    def _image_provider_configured(self) -> bool:
        return self.ai_model_gateway.image_configured()

    async def _create_remote_workflow(
        self,
        request: IndustrialDesignWorkflowRequest,
        *,
        auth_user: dict[str, object] | None = None,
    ) -> dict[str, JSONValue]:
        del request, auth_user
        raise CadAiGatewayError(
            (
                "远端 CAD AI 工作流已禁用：当前协议不能可靠回收全部生成文件"
                "并写入 PostgreSQL。"
            ),
            "CAD_AI_DATABASE_ASSET_RECOVERY_UNAVAILABLE",
            status_code=503,
        )

    async def _create_local_workflow(
        self,
        request: IndustrialDesignWorkflowRequest,
        *,
        user_id: str,
        db: Session,
        workflow_id: str,
    ) -> dict[str, JSONValue]:
        project_name = request.project_name or self._default_project_name(request)
        design_spec = self._build_design_spec(request)
        outputs: dict[str, JSONValue] = {}
        diagnostics: list[dict[str, str]] = []

        if request.options.generate_drawing:
            try:
                drawing_result = self.drawing_service.render_and_store(
                    db=db,
                    user_id=user_id,
                    request=self._build_default_drawing_request(
                        project_name,
                        request.industry,
                    ),
                    task_id=workflow_id,
                    project_id=project_name,
                    publish_assets=False,
                )
                drawing_data = drawing_result.model_dump(by_alias=True)
                outputs.update({
                    "drawingId": drawing_data.get("drawingId"),
                    "drawingSvgAssetId": drawing_data.get("svgAssetId"),
                    "drawingSvg": drawing_data.get("svgUrl"),
                    "drawingPdfAssetId": drawing_data.get("pdfAssetId"),
                    "drawingPdf": drawing_data.get("pdfUrl"),
                    "drawingDxfAssetId": drawing_data.get("dxfAssetId"),
                    "drawingDxf": drawing_data.get("dxfUrl"),
                    "drawingSummary": drawing_data.get("summary"),
                })
            except Exception as exc:
                logger.exception("本地工程图生成或资产持久化失败")
                diagnostics.append({
                    "level": "warning",
                    "title": "工程图生成失败",
                    "detail": "工程图生成或资产持久化失败，请稍后重试。",
                })

        if request.options.generate_cad:
            if self._cad_provider(request) == "build123d":
                try:
                    build123d_result = await self.build123d_service.generate_model(
                        prompt=self._build_build123d_prompt(project_name, request, design_spec),
                        db=db,
                        user_id=user_id,
                        task_id=workflow_id,
                        publish_assets=False,
                        render_views=request.options.generate_render_views,
                    )
                    outputs.update({
                        "modelScriptAssetId": build123d_result.get("modelScriptAssetId"),
                        "modelScript": (
                            self._asset_url(str(build123d_result["modelScriptAssetId"]))
                            if build123d_result.get("modelScriptAssetId")
                            else None
                        ),
                        "modelStepAssetId": build123d_result.get("modelStepAssetId"),
                        "modelStep": build123d_result.get("modelStep"),
                        "modelStlAssetId": build123d_result.get("modelStlAssetId"),
                        "modelStl": build123d_result.get("modelStl"),
                        "modelGlbAssetId": build123d_result.get("modelGlbAssetId"),
                        "modelGlb": build123d_result.get("modelGlb"),
                        "modelDownloadUrl": build123d_result.get("modelDownloadUrl"),
                        "build123dTaskId": build123d_result.get("taskId"),
                    })
                    if build123d_result.get("renderViews"):
                        outputs["renderViews"] = build123d_result["renderViews"]
                        outputs["renderViewsPreview"] = build123d_result.get("renderViewsPreview")
                except Build123dServiceError as exc:
                    logger.warning(
                        "本地 build123d 生成失败，error_code=%s",
                        exc.error_code,
                        exc_info=True,
                    )
                    diagnostics.append({
                        "level": "warning",
                        "title": "3D 模型生成失败",
                        "detail": "3D 模型生成失败，请检查服务配置后重试。",
                    })
                except Exception as exc:
                    logger.exception("本地 build123d 资产持久化失败")
                    diagnostics.append({
                        "level": "error",
                        "title": "CAD 资产入库失败",
                        "detail": "CAD 资产持久化失败，请稍后重试。",
                    })
            else:
                try:
                    forgecad_request = ForgeCadGenerateRequest(
                        prompt=self._build_forgecad_prompt(project_name, request, design_spec),
                        exportFormat="none",
                        runCli=True,
                        action="create" if request.mode == "create" else "derive",
                        sourceObject=project_name,
                    )
                    forgecad_result = await self.ai_model_gateway.generate_cad(
                        forgecad_request,
                        db=db,
                        user_id=user_id,
                        task_id=workflow_id,
                        publish_assets=False,
                    )
                    forgecad_data = forgecad_result.model_dump(by_alias=True)
                    outputs.update({
                        "modelScriptAssetId": forgecad_data.get("scriptAssetId"),
                        "modelScript": (
                            self._asset_url(str(forgecad_data["scriptAssetId"]))
                            if forgecad_data.get("scriptAssetId")
                            else None
                        ),
                        "modelOutputAssetId": forgecad_data.get("outputAssetId"),
                        "modelDownloadUrl": forgecad_data.get("downloadUrl"),
                        "forgecadTaskId": forgecad_data.get("taskId"),
                    })
                except ForgeCadServiceError as exc:
                    logger.warning(
                        "本地 CAD 脚本生成失败，error_code=%s",
                        exc.error_code,
                        exc_info=True,
                    )
                    diagnostics.append({
                        "level": "warning",
                        "title": "CAD 脚本生成失败",
                        "detail": "CAD 脚本生成失败，请检查服务配置后重试。",
                    })
                except Exception as exc:
                    logger.exception("本地 CAD 资产持久化失败")
                    diagnostics.append({
                        "level": "error",
                        "title": "CAD 资产入库失败",
                        "detail": "CAD 资产持久化失败，请稍后重试。",
                    })

        if request.options.generate_plan_line and self.build123d_service.available:
            try:
                line_result = await self.build123d_service.generate_plan_line(
                    prompt=self._build_build123d_prompt(project_name, request, design_spec),
                    db=db,
                    user_id=user_id,
                    task_id=workflow_id,
                    publish_assets=False,
                )
                outputs.update({
                    "planLineSvgAssetId": line_result.get("planLineSvgAssetId"),
                    "planLineDxfAssetId": line_result.get("planLineDxfAssetId"),
                    "planLine": line_result.get("planLine"),
                    "planLineDxf": line_result.get("planLineDxf"),
                    "planLineTaskId": line_result.get("taskId"),
                })
            except Build123dServiceError as exc:
                logger.warning(
                    "本地 CAD 线图生成失败，error_code=%s",
                    exc.error_code,
                    exc_info=True,
                )
                diagnostics.append({
                    "level": "warning",
                    "title": "2D 线图生成失败",
                    "detail": "2D CAD 线图生成失败，请检查服务配置后重试。",
                })
            except Exception as exc:
                logger.exception("本地 CAD 线图资产持久化失败")
                diagnostics.append({
                    "level": "error",
                    "title": "2D 线图入库失败",
                    "detail": "2D 线图资产持久化失败，请稍后重试。",
                })

        if request.options.generate_three_preview and request.options.generate_cad:
            outputs["threePreview"] = self._build_three_preview_spec(project_name, request)
        if request.options.generate_render:
            outputs.setdefault("renderPng", None)
        if request.options.generate_explosion:
            outputs.setdefault("explosionPng", None)
        if request.options.enhance_image and (
            request.options.generate_render or request.options.generate_explosion
        ):
            image_edit_result = await self._try_generate_enhanced_image(
                request=request,
                project_name=project_name,
                design_spec=design_spec,
                diagnostics=diagnostics,
                user_id=user_id,
                db=db,
                task_id=workflow_id,
                publish_assets=False,
            )
            if image_edit_result:
                self._apply_image_edit_result(
                    outputs,
                    image_edit_result,
                    output_kind=self._image_edit_output_kind(request),
                )

        missing_assets = self._missing_required_asset_outputs(
            request,
            outputs,
            require_engineering_drawing_formats=True,
        )
        failed = bool(missing_assets)
        if failed:
            detail = f"以下必需资产未成功入库：{'、'.join(missing_assets)}"
            diagnostics.append({
                "level": "error",
                "title": "必需资产持久化失败",
                "detail": detail,
            })
        return {
            "taskId": workflow_id,
            "status": "failed" if failed else "completed",
            "progress": 100,
            "currentStep": (
                "工业品设计工作流失败：必需资产未成功写入数据库。"
                if failed
                else "工业品设计工作流已完成，可继续修改或导出。"
            ),
            "sourceMode": request.input_type,
            "projectId": project_name,
            "versionId": f"v_{workflow_id[-8:]}",
            "designSpec": design_spec,
            "outputs": outputs,
            "diagnostics": diagnostics,
            "errorCode": (
                "REQUIRED_ASSET_PERSISTENCE_FAILED"
                if failed
                else None
            ),
            "errorMessage": (
                f"必需资产未成功入库：{'、'.join(missing_assets)}"
                if failed
                else None
            ),
            "recoverable": not failed,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _missing_required_asset_outputs(
        request: IndustrialDesignWorkflowRequest,
        outputs: dict[str, object],
        *,
        require_engineering_drawing_formats: bool,
    ) -> list[str]:
        drawing_requirements = (
            [
                (request.options.generate_drawing, "drawingSvgAssetId", "SVG 工程图"),
                (request.options.generate_drawing, "drawingPdfAssetId", "PDF 工程图"),
                (request.options.generate_drawing, "drawingDxfAssetId", "DXF 工程图"),
            ]
            if require_engineering_drawing_formats
            else [
                (request.options.generate_drawing, "renderPngAssetId", "设计图"),
            ]
        )
        requirements: list[tuple[bool, str, str]] = [
            *drawing_requirements,
            (request.options.generate_cad, "modelScriptAssetId", "ForgeCAD 脚本"),
            (request.options.generate_plan_line, "planLineSvgAssetId", "2D 线图"),
            (request.options.generate_render, "renderPngAssetId", "设计效果图"),
            (request.options.generate_explosion, "explosionPngAssetId", "爆炸图"),
        ]
        return [
            label
            for required, key, label in requirements
            if required and not isinstance(outputs.get(key), str)
        ]

    async def _enqueue_external_workflow(
        self,
        request: IndustrialDesignWorkflowRequest,
        *,
        auth_user: dict[str, object] | None = None,
        schedule: bool = True,
    ) -> dict[str, JSONValue]:
        project_name = request.project_name or self._default_project_name(request)
        design_spec = self._build_design_spec(request)
        workflow_id = f"industrial_design_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        current_step = "工业品设计任务已提交，正在等待处理。"
        if request.options.generate_plan_line:
            current_step = "工业品设计任务已提交，正在生成 2D CAD 线图。"
        elif request.options.generate_drawing:
            current_step = "工业品设计任务已提交，正在生成设计图。"
        elif request.options.generate_render:
            current_step = "工业品设计任务已提交，正在生成精修图。"
        elif request.options.generate_cad:
            provider_label = "build123d" if self._cad_provider(request) == "build123d" else "ForgeCAD"
            current_step = f"工业品设计任务已提交，正在调用本地部署 {provider_label} 生成 3D/CAD。"

        pending_task = {
            "taskId": workflow_id,
            "status": "pending",
            "progress": 5,
            "currentStep": current_step,
            "sourceMode": request.input_type,
            "projectId": project_name,
            "versionId": f"v_{workflow_id[-8:]}",
            "designSpec": {
                **design_spec,
                "pipeline": "nodapi_image_plus_local_forgecad",
                "pipelineNote": "当前 Zoo 公开接口为 text-to-CAD，3D 结果基于统一设计描述生成，不是直接由 2D 位图反推几何。",
            },
            "outputs": {},
            "diagnostics": [],
            "createdAt": now,
            "updatedAt": now,
        }
        await self._persist_created_task(request, pending_task, auth_user)
        if schedule:
            self._schedule(
                self._run_external_workflow(
                    workflow_id,
                    request,
                    project_name,
                    design_spec,
                )
            )
        return deepcopy(pending_task)

    async def _run_external_workflow(
        self,
        workflow_id: str,
        request: IndustrialDesignWorkflowRequest,
        project_name: str,
        design_spec: dict[str, JSONValue],
    ) -> None:
        try:
            leased = await run_in_threadpool(self._acquire_lease_sync, workflow_id)
        except Exception:
            logger.exception("工作流获取租约失败: %s", workflow_id)
            return
        if leased is None:
            return
        outputs: dict[str, JSONValue] = {}
        diagnostics: list[dict[str, str]] = []
        stop_heartbeat = asyncio.Event()
        lease_lost = asyncio.Event()
        execution = asyncio.create_task(
            self._execute_external_workflow(
                workflow_id,
                request,
                project_name,
                design_spec,
                outputs,
                diagnostics,
            )
        )
        heartbeat = asyncio.create_task(
            self._lease_heartbeat(
                workflow_id,
                execution,
                stop_heartbeat,
                lease_lost,
            )
        )
        try:
            await execution
        except asyncio.CancelledError:
            if not lease_lost.is_set():
                raise
        except Exception as exc:
            if lease_lost.is_set():
                return
            logger.exception("工业品设计工作流执行失败: %s", workflow_id)
            failed_task = await self._update_task(
                workflow_id,
                status="failed",
                progress=100,
                current_step="工业品设计工作流执行失败，请稍后重试。",
                diagnostics=[
                    *diagnostics,
                    {
                        "level": "error",
                        "title": "工作流执行失败",
                        "detail": "工作流执行失败，请稍后重试。",
                    },
                ],
                error_code="INDUSTRIAL_DESIGN_WORKFLOW_FAILED",
                error_message="工作流执行失败，请稍后重试。",
                recoverable=False,
            )
            if failed_task is not None:
                await self._persist_terminal_history_if_needed(
                    failed_task,
                    request,
                    None,
                )
        finally:
            stop_heartbeat.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                await run_in_threadpool(self._release_lease_sync, workflow_id)
            except Exception:
                logger.exception("工作流释放租约失败: %s", workflow_id)

    async def _lease_heartbeat(
        self,
        workflow_id: str,
        execution: asyncio.Task[None],
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        loop = asyncio.get_running_loop()
        interval = max(1.0, self.lease_seconds / 3)
        deadline = loop.time() + self.lease_seconds
        while not stop.is_set() and not execution.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                lease_lost.set()
                execution.cancel()
                return
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=min(interval, remaining),
                )
                return
            except TimeoutError:
                pass
            if stop.is_set() or execution.done():
                return
            try:
                renewed = await run_in_threadpool(
                    self._renew_lease_sync,
                    workflow_id,
                )
            except Exception:
                logger.exception("工作流租约心跳暂时失败: %s", workflow_id)
                continue
            if renewed is None:
                return
            if not renewed:
                lease_lost.set()
                execution.cancel()
                return
            deadline = loop.time() + self.lease_seconds

    async def _execute_external_workflow(
        self,
        workflow_id: str,
        request: IndustrialDesignWorkflowRequest,
        project_name: str,
        design_spec: dict[str, JSONValue],
        outputs: dict[str, JSONValue],
        diagnostics: list[dict[str, str]],
    ) -> None:
        user_id = await run_in_threadpool(
            self._get_task_user_id_sync,
            workflow_id,
        )
        image_prompt = self._build_image_prompt(project_name, request, design_spec)
        forgecad_prompt = self._build_forgecad_prompt(project_name, request, design_spec)
        cad_prompt = self._build_zoo_prompt(project_name, request, design_spec)
        reference_urls = list(dict.fromkeys([
            *[url for url in request.asset_urls if isinstance(url, str) and url.strip()],
            *[item.preview_asset_url for item in request.asset_metas if item.preview_asset_url],
        ]))
        reference_urls = await self._resolve_reference_image_urls(reference_urls, user_id)

        if request.options.generate_drawing or request.options.generate_render:
            step_label = "设计图" if request.options.generate_drawing and not request.options.generate_render else "宣发图"
            image_edit_succeeded = False
            if request.options.generate_render and request.options.enhance_image:
                await self._update_task(workflow_id, progress=20, current_step="正在基于参考设计图生成场景融合宣发图。")
                image_edit_result = await self._try_generate_enhanced_image(
                    request=request,
                    project_name=project_name,
                    design_spec=design_spec,
                    diagnostics=diagnostics,
                    user_id=user_id,
                    task_id=workflow_id,
                    publish_assets=False,
                )
                if image_edit_result:
                    self._apply_image_edit_result(
                        outputs,
                        image_edit_result,
                        output_kind=self._image_edit_output_kind(request),
                    )
                    await self._update_task(
                        workflow_id,
                        progress=80,
                        current_step="已基于参考设计图生成场景融合宣发图。",
                        outputs=outputs,
                        diagnostics=diagnostics,
                    )
                    image_edit_succeeded = True
            if not image_edit_succeeded:
                try:
                    provider_label = self._image_provider_label()
                    await self._update_task(workflow_id, progress=20, current_step=f"正在调用 {provider_label} 生成{step_label}。")
                    image_result = await self._generate_external_design_image(
                        prompt=image_prompt,
                        images=reference_urls or None,
                        optimize_prompt=request.options.optimize_prompt,
                        image_model=getattr(request.options, 'image_model', None),
                        image_provider=getattr(request.options, 'image_provider', None),
                    )
                    image_url = str(image_result.get("resultUrl") or "").strip()
                    if not image_url:
                        raise NodApiImageServiceError(
                            f"{step_label}生成完成但未返回可用图片地址",
                            "DESIGN_IMAGE_URL_MISSING",
                            status_code=502,
                        )
                    image_asset_id = await self._persist_generated_image_url(
                        user_id=user_id,
                        image_url=image_url,
                        task_id=workflow_id,
                        publish_asset=False,
                    )
                    asset_url = self._asset_url(image_asset_id)
                    outputs["renderPngAssetId"] = image_asset_id
                    outputs["renderPng"] = asset_url
                    outputs["enhancedImageAssetId"] = image_asset_id
                    outputs["enhancedImage"] = asset_url
                    outputs["imageTaskId"] = image_result["taskId"]
                    outputs["imageProvider"] = image_result["model"]
                    if image_result.get("promptMeta"):
                        outputs["imagePromptMeta"] = image_result["promptMeta"]
                except (NodApiImageServiceError, NodApiMidjourneyServiceError, GeminiImageServiceError, DashScopeImageServiceError) as exc:
                    logger.warning(
                        "%s生成失败，error_code=%s",
                        step_label,
                        exc.error_code,
                        exc_info=True,
                    )
                    diagnostics.append({
                        "level": "warning",
                        "title": f"{step_label}生成失败",
                        "detail": f"{step_label}生成失败，请稍后重试。",
                    })
                    await self._update_task(workflow_id, progress=35, current_step=f"{step_label}生成失败。", diagnostics=diagnostics)
                except Exception as exc:
                    logger.exception("%s资产持久化失败: %s", step_label, workflow_id)
                    diagnostics.append({
                        "level": "warning",
                        "title": f"{step_label}资产入库失败",
                        "detail": f"{step_label}资产持久化失败，请稍后重试。",
                    })
                    await self._update_task(
                        workflow_id,
                        progress=35,
                        current_step=f"{step_label}资产入库失败。",
                        diagnostics=diagnostics,
                    )

        if request.options.generate_plan_line:
            try:
                await self._update_task(workflow_id, progress=15, current_step="正在基于需求生成 2D CAD 线图。")
                with self.db_context_factory() as db:
                    line_result = await self.build123d_service.generate_plan_line(
                        prompt=self._build_build123d_prompt(project_name, request, design_spec),
                        db=db,
                        user_id=user_id,
                        task_id=workflow_id,
                        publish_assets=False,
                    )
                outputs.update({
                    "planLineSvgAssetId": line_result.get("planLineSvgAssetId"),
                    "planLineDxfAssetId": line_result.get("planLineDxfAssetId"),
                    "planLine": line_result.get("planLine"),
                    "planLineDxf": line_result.get("planLineDxf"),
                    "planLineTaskId": line_result.get("taskId"),
                })
                await self._update_task(
                    workflow_id,
                    progress=25,
                    current_step="2D CAD 线图已生成。",
                    outputs=outputs,
                )
            except Build123dServiceError as exc:
                logger.warning(
                    "2D CAD 线图生成失败，error_code=%s",
                    exc.error_code,
                    exc_info=True,
                )
                diagnostics.append({
                    "level": "warning",
                    "title": "2D CAD 线图生成失败",
                    "detail": "2D CAD 线图生成失败，请稍后重试。",
                })
                await self._update_task(
                    workflow_id,
                    progress=20,
                    current_step="2D CAD 线图生成失败。",
                    outputs=outputs,
                    diagnostics=diagnostics,
                )

        if request.options.generate_cad:
            if self._cad_provider(request) == "build123d":
                try:
                    await self._update_task(workflow_id, progress=35, current_step="正在调用 build123d 生成 3D 模型。")
                    with self.db_context_factory() as db:
                        build123d_result = await self.build123d_service.generate_model(
                            prompt=self._build_build123d_prompt(project_name, request, design_spec),
                            db=db,
                            user_id=user_id,
                            task_id=workflow_id,
                            publish_assets=False,
                            render_views=request.options.generate_render_views,
                        )
                    outputs.update({
                        "modelScriptAssetId": build123d_result.get("modelScriptAssetId"),
                        "modelScript": (
                            self._asset_url(str(build123d_result["modelScriptAssetId"]))
                            if build123d_result.get("modelScriptAssetId")
                            else None
                        ),
                        "modelStepAssetId": build123d_result.get("modelStepAssetId"),
                        "modelStep": build123d_result.get("modelStep"),
                        "modelStlAssetId": build123d_result.get("modelStlAssetId"),
                        "modelStl": build123d_result.get("modelStl"),
                        "modelGlbAssetId": build123d_result.get("modelGlbAssetId"),
                        "modelGlb": build123d_result.get("modelGlb"),
                        "modelDownloadUrl": build123d_result.get("modelDownloadUrl"),
                        "build123dTaskId": build123d_result.get("taskId"),
                    })
                    if build123d_result.get("renderViews"):
                        outputs["renderViews"] = build123d_result["renderViews"]
                        outputs["renderViewsPreview"] = build123d_result.get("renderViewsPreview")
                    await self._update_task(
                        workflow_id,
                        progress=80,
                        current_step="build123d 已完成 3D 模型生成。",
                        outputs=outputs,
                    )
                except Build123dServiceError as exc:
                    logger.warning(
                        "build123d 生成失败，error_code=%s",
                        exc.error_code,
                        exc_info=True,
                    )
                    diagnostics.append({
                        "level": "warning",
                        "title": "3D 模型生成失败",
                        "detail": "3D 模型生成失败，正在尝试备用能力。",
                    })
                    await self._update_task(
                        workflow_id,
                        progress=70,
                        current_step="build123d 生成失败，继续尝试 Zoo 3D/CAD。",
                        outputs=outputs,
                        diagnostics=diagnostics,
                    )
            else:
                if request.options.generate_cad:
                    await self._update_task(workflow_id, progress=35, current_step="正在调用本地部署 ForgeCAD 生成 3D/CAD。")
            try:
                forgecad_request = ForgeCadGenerateRequest(
                    prompt=forgecad_prompt,
                    exportFormat="none",
                    runCli=True,
                    action="create" if request.mode == "create" else "derive",
                    sourceObject=project_name,
                )
                with self.db_context_factory() as db:
                    forgecad_result = await self.ai_model_gateway.generate_cad(
                        forgecad_request,
                        db=db,
                        user_id=user_id,
                        task_id=workflow_id,
                        publish_assets=False,
                    )
                forgecad_data = forgecad_result.model_dump(by_alias=True)
                outputs.update({
                    "modelScriptAssetId": forgecad_data.get("scriptAssetId"),
                    "modelScript": (
                        self._asset_url(str(forgecad_data["scriptAssetId"]))
                        if forgecad_data.get("scriptAssetId")
                        else None
                    ),
                    "modelOutputAssetId": forgecad_data.get("outputAssetId"),
                    "modelDownloadUrl": forgecad_data.get("downloadUrl"),
                    "forgecadTaskId": forgecad_data.get("taskId"),
                })
                await self._update_task(
                    workflow_id,
                    progress=80,
                    current_step="本地部署 ForgeCAD 已完成 3D/CAD 脚本生成。",
                    outputs=outputs,
                )
            except ForgeCadServiceError as exc:
                logger.warning(
                    "外部链路 ForgeCAD 生成失败，error_code=%s",
                    exc.error_code,
                    exc_info=True,
                )
                diagnostics.append({
                    "level": "warning",
                    "title": "本地部署 ForgeCAD 生成失败",
                    "detail": "本地部署 ForgeCAD 生成失败，正在尝试备用能力。",
                })
                await self._update_task(
                    workflow_id,
                    progress=70,
                    current_step="本地部署 ForgeCAD 生成失败，继续尝试 Zoo 3D/CAD。",
                    outputs=outputs,
                    diagnostics=diagnostics,
                )

        if not outputs.get("modelDownloadUrl") and request.options.generate_cad:
            try:
                zoo_result = await self.ai_model_gateway.create_text_to_cad(
                    prompt=cad_prompt,
                    project_name=project_name,
                )
                with self.db_context_factory() as db:
                    saved_assets = self.persist_zoo_outputs(
                        db=db,
                        user_id=user_id,
                        project_name=project_name,
                        outputs=cast(dict[str, object], zoo_result["outputs"]),
                        task_id=workflow_id,
                        publish_assets=False,
                    )
                if saved_assets.get("glb"):
                    outputs["modelGlbAssetId"] = saved_assets["glb"]
                    outputs["modelGlb"] = self._asset_url(saved_assets["glb"])
                if saved_assets.get("step"):
                    outputs["modelStepAssetId"] = saved_assets["step"]
                    outputs["modelStep"] = self._asset_url(saved_assets["step"])
                outputs["zooTaskId"] = zoo_result["taskId"]
            except ZooDesignServiceError as exc:
                logger.warning(
                    "Zoo 3D/CAD 生成失败，error_code=%s",
                    exc.error_code,
                    exc_info=True,
                )
                diagnostics.append({
                    "level": "warning",
                    "title": "3D/CAD 生成失败",
                    "detail": "3D/CAD 生成失败，请稍后重试。",
                })
                await self._update_task(
                    workflow_id,
                    progress=75,
                    current_step="Zoo 3D/CAD 生成失败。",
                    outputs=outputs,
                    diagnostics=diagnostics,
                )

        if request.options.generate_three_preview and request.options.generate_cad:
            outputs.setdefault("threePreview", self._build_three_preview_spec(project_name, request))
        if request.options.generate_explosion and not request.options.enhance_image:
            try:
                await self._update_task(
                    workflow_id,
                    progress=40,
                    current_step="正在生成平面爆炸分解图。",
                )
                explosion_prompt = self._build_explosion_image_prompt(project_name, request, design_spec)
                explosion_result = await self._generate_external_design_image(
                    prompt=explosion_prompt,
                    images=reference_urls or None,
                    optimize_prompt=request.options.optimize_prompt,
                    image_model=getattr(request.options, 'image_model', None),
                    image_provider=getattr(request.options, 'image_provider', None),
                )
                explosion_url = str(explosion_result.get("resultUrl") or "").strip()
                if explosion_url:
                    explosion_asset_id = await self._persist_generated_image_url(
                        user_id=user_id,
                        image_url=explosion_url,
                        task_id=workflow_id,
                        publish_asset=False,
                    )
                    outputs["explosionPngAssetId"] = explosion_asset_id
                    outputs["explosionPng"] = self._asset_url(explosion_asset_id)
                    outputs["explosionTaskId"] = explosion_result.get("taskId")
                    outputs["explosionProvider"] = explosion_result.get("model")
            except (NodApiImageServiceError, NodApiMidjourneyServiceError, GeminiImageServiceError, DashScopeImageServiceError) as exc:
                logger.warning(
                    "爆炸图生成失败，error_code=%s",
                    exc.error_code,
                    exc_info=True,
                )
                diagnostics.append({
                    "level": "warning",
                    "title": "爆炸图生成失败",
                    "detail": "爆炸分解图生成失败，可稍后重试。",
                })
            except Exception as exc:
                logger.exception("爆炸图资产持久化失败")
                diagnostics.append({
                    "level": "warning",
                    "title": "爆炸图入库失败",
                    "detail": "爆炸分解图资产持久化失败，可稍后重试。",
                })
        if (
            request.options.enhance_image
            and (request.options.generate_render or request.options.generate_explosion)
            and not outputs.get("enhancedImage")
            and not outputs.get("explosionPng")
        ):
            image_edit_result = await self._try_generate_enhanced_image(
                request=request,
                project_name=project_name,
                design_spec=design_spec,
                diagnostics=diagnostics,
                user_id=user_id,
                task_id=workflow_id,
                publish_assets=False,
            )
            if image_edit_result:
                self._apply_image_edit_result(
                    outputs,
                    image_edit_result,
                    output_kind=self._image_edit_output_kind(request),
                )

        missing_assets = self._missing_required_asset_outputs(
            request,
            outputs,
            require_engineering_drawing_formats=False,
        )
        if missing_assets:
            diagnostics.append({
                "level": "error",
                "title": "必需资产持久化失败",
                "detail": f"以下必需资产未成功入库：{'、'.join(missing_assets)}",
            })
        status = (
            "completed"
            if not missing_assets
            and (
                outputs.get("renderPng")
            or outputs.get("explosionPng")
            or outputs.get("drawingSvg")
            or outputs.get("planLine")
            or outputs.get("modelGlb")
            or outputs.get("modelStep")
            or outputs.get("modelDownloadUrl")
                or not any(
                    (
                        request.options.generate_drawing,
                        request.options.generate_cad,
                        request.options.generate_plan_line,
                        request.options.generate_render,
                        request.options.generate_explosion,
                    )
                )
            )
            else "failed"
        )
        if status == "completed":
            if outputs.get("renderPng"):
                current_step = "设计图已生成，可在下方预览和继续修改。"
            elif outputs.get("explosionPng"):
                current_step = "爆炸分解图已生成，可在下方预览和继续修改。"
            elif outputs.get("planLine"):
                current_step = "2D CAD 线图已生成，可在下方预览和下载。"
            elif outputs.get("drawingSvg"):
                current_step = "已生成本地工程图，可在下方预览和下载。"
            elif outputs.get("modelGlb") or outputs.get("modelStep") or outputs.get("modelDownloadUrl"):
                current_step = "已基于已有设计输入生成 3D/CAD 结果。"
            else:
                current_step = "工业品设计工作流已完成。"
        else:
            warning_detail = next(
                (
                    item.get("detail")
                    for item in diagnostics
                    if item.get("level") in {"warning", "error"} and item.get("detail")
                ),
                None,
            )
            current_step = f"设计图生成失败：{warning_detail}" if warning_detail else "外部设计链路未生成有效结果。"
        terminal_task = await self._update_task(
            workflow_id,
            status=status,
            progress=100 if status == "completed" else 100,
            current_step=current_step,
            outputs=outputs,
            diagnostics=diagnostics,
            error_code=(
                "REQUIRED_ASSET_PERSISTENCE_FAILED"
                if missing_assets
                else None
            ),
            error_message=(
                f"必需资产未成功入库：{'、'.join(missing_assets)}"
                if missing_assets
                else None
            ),
            recoverable=False if missing_assets else None,
        )
        if terminal_task is not None:
            await self._persist_terminal_history_if_needed(
                terminal_task,
                request,
                None,
            )

    def _build_remote_payload(self, request: IndustrialDesignWorkflowRequest) -> dict[str, JSONValue]:
        payload = request.model_dump(by_alias=True)
        payload["workflow"] = "industrial_product_design"
        payload["options"] = {
            **payload.get("options", {}),
        }
        payload["designSpec"] = self._build_design_spec(request)
        return payload

    def _build_design_spec(self, request: IndustrialDesignWorkflowRequest) -> dict[str, JSONValue]:
        text = (request.text or "").strip()
        asset_summaries = [
            f"{item.filename}({item.extension})：{item.parse_message or item.parse_status}"
            for item in request.asset_metas
        ]
        return {
            "inputType": request.input_type,
            "mode": request.mode,
            "projectName": request.project_name or self._default_project_name(request),
            "industry": request.industry or "装备制造",
            "requirementText": text,
            "assetSummaries": asset_summaries,
            "targetOutputs": ["工程图", "3D预览", "效果图", "爆炸图"],
        }

    async def _persist_created_task(
        self,
        request: IndustrialDesignWorkflowRequest,
        task: dict[str, JSONValue],
        auth_user: dict[str, object] | None,
    ) -> None:
        await run_in_threadpool(
            self._persist_created_task_sync,
            request,
            task,
            self._resolve_user_id(auth_user),
        )

    def _persist_created_task_sync(
        self,
        request: IndustrialDesignWorkflowRequest,
        task: dict[str, JSONValue],
        user_id: str,
    ) -> None:
        with self.db_context_factory() as db:
            self.repository_factory(db).create(user_id, request, task)

    async def _persist_terminal_history_if_needed(
        self,
        task: dict[str, JSONValue],
        request: IndustrialDesignWorkflowRequest | None,
        auth_user: dict[str, object] | None,
    ) -> None:
        if task.get("status") not in {"completed", "failed"}:
            return
        try:
            await run_in_threadpool(
                self._persist_terminal_history_sync,
                str(task["taskId"]),
                request,
                auth_user,
            )
        except Exception:
            logger.exception(
                "工作流历史归档失败，保留 history_persisted=false 等待启动补偿: %s",
                task["taskId"],
            )

    def _persist_terminal_history_sync(
        self,
        task_id: str,
        request: IndustrialDesignWorkflowRequest | None,
        auth_user: dict[str, object] | None,
    ) -> None:
        history_lock = _HISTORY_LOCKS[hash(task_id) % len(_HISTORY_LOCKS)]
        with self.db_context_factory() as db:
            bind = db.get_bind()
            sqlite_serialization = (
                history_lock
                if bind.dialect.name == "sqlite"
                else nullcontext()
            )
            with sqlite_serialization:
                repository = self.repository_factory(db)
                task = repository.get_terminal_for_history(task_id)
                if task is None:
                    return
                user_id = str(task["userId"])
                effective_user = auth_user or {
                    "sub": user_id,
                    "username": user_id,
                    "displayName": user_id,
                }
                project_payload = self._build_project_payload(task, request)
                with self.history_service.transaction_lock(
                    db,
                    user_id=user_id,
                    project_id=project_payload.id,
                ):
                    try:
                        self.history_service.upsert_project_with_version_in_transaction(
                            db,
                            auth_user=effective_user,
                            project_payload=project_payload,
                            version_payload=self._build_version_payload(task, request),
                        )
                        repository.mark_history_persisted(task_id, user_id)
                        db.commit()
                    except Exception:
                        db.rollback()
                        raise

    def _get_task_sync(
        self,
        task_id: str,
        user_id: str,
    ) -> dict[str, JSONValue] | None:
        with self.db_context_factory() as db:
            task = self.repository_factory(db).get(task_id, user_id)
            return cast(dict[str, JSONValue], task) if task is not None else None

    def _get_task_user_id_sync(self, task_id: str) -> str:
        with self.db_context_factory() as db:
            task = self.repository_factory(db).get_internal(task_id)
            if task is None:
                raise RuntimeError(f"workflow task not found: {task_id}")
            return str(task["userId"])

    def _acquire_lease_sync(self, task_id: str) -> dict[str, object] | None:
        with self.db_context_factory() as db:
            return self.repository_factory(db).acquire_lease(
                task_id,
                self.worker_id,
                datetime.now(timezone.utc),
                self.lease_seconds,
            )

    def _release_lease_sync(self, task_id: str) -> None:
        with self.db_context_factory() as db:
            self.repository_factory(db).release_lease(task_id, self.worker_id)

    def _renew_lease_sync(self, task_id: str) -> bool | None:
        with self.db_context_factory() as db:
            repository = self.repository_factory(db)
            renewed = repository.renew_lease(
                task_id,
                self.worker_id,
                datetime.now(timezone.utc),
                self.lease_seconds,
            )
            if renewed:
                return True
            task = repository.get_internal(task_id)
            if (
                task is not None
                and task.get("leaseOwner") == self.worker_id
                and task.get("status") in {"completed", "failed"}
            ):
                return None
            return False

    def _build_project_payload(
        self,
        task: dict[str, JSONValue],
        request: IndustrialDesignWorkflowRequest | None,
    ) -> ProjectRecordPayload:
        project_name = str(task.get("projectId") or (request.project_name if request else "") or "未命名项目")
        project_id = self._build_project_history_id(project_name)
        result_text = str(task.get("currentStep") or task.get("error") or "").strip()
        preview_image = self._resolve_preview_image(task)
        created_at = str(task.get("createdAt") or datetime.now(timezone.utc).isoformat())
        updated_at = str(task.get("updatedAt") or created_at)
        return ProjectRecordPayload(
            id=project_id,
            name=project_name,
            industry=(request.industry if request else str(task.get("designSpec", {}).get("industry") or "装备制造")),
            description=(request.text or "") if request else "",
            inputMode=str(task.get("sourceMode") or (request.input_type if request else "prompt")),
            createdAt=created_at,
            updatedAt=updated_at,
            lastTaskId=str(task.get("taskId") or ""),
            lastStatus=str(task.get("status") or ""),
            lastResultText=result_text,
            lastImageUrl=preview_image,
            versionCount=self._extract_version_number(task),
        )

    def _build_version_payload(
        self,
        task: dict[str, JSONValue],
        request: IndustrialDesignWorkflowRequest | None,
    ) -> VersionSnapshotPayload:
        outputs = task.get("outputs") if isinstance(task.get("outputs"), dict) else {}
        completed = str(task.get("status") or "") == "completed"
        generated_assets = self._collect_generated_assets(outputs) if completed else []
        script_asset_id = (
            self._valid_asset_id(outputs.get("modelScriptAssetId"))
            if completed
            else None
        )
        output_asset_id = self._first_output_asset_id(outputs) if completed else None
        download_url = (
            self._asset_url(output_asset_id)
            if output_asset_id is not None
            else None
        )
        preview_image = self._resolve_preview_image(task) if completed else None
        generated_image_urls = [preview_image] if preview_image else []
        created_at = str(task.get("updatedAt") or task.get("createdAt") or datetime.now(timezone.utc).isoformat())
        return VersionSnapshotPayload(
            id=self._build_version_id(task),
            label=str(task.get("projectId") or (request.project_name if request else "") or "未命名项目"),
            status="已完成" if str(task.get("status") or "") == "completed" else str(task.get("status") or ""),
            note=str(task.get("currentStep") or task.get("error") or ""),
            projectId=self._build_project_history_id(str(task.get("projectId") or (request.project_name if request else "") or "未命名项目")),
            projectName=str(task.get("projectId") or (request.project_name if request else "") or "未命名项目"),
            versionNumber=self._extract_version_number(task),
            isFinalized=False,
            sourceProjectId=self._build_project_history_id(str(task.get("projectId") or (request.project_name if request else "") or "未命名项目")),
            prompt=request.text if request else None,
            resultText=str(task.get("currentStep") or task.get("error") or ""),
            previewImageUrl=preview_image,
            generatedImageUrls=generated_image_urls,
            changeType="方案生成",
            sourceObject=str(task.get("projectId") or (request.project_name if request else "") or "未命名项目"),
            taskId=str(task.get("taskId") or ""),
            scriptAssetId=script_asset_id,
            outputAssetId=output_asset_id,
            downloadUrl=download_url,
            executionSummary=f"项目「{task.get('projectId') or ''}」{task.get('status') or ''}，进度 {task.get('progress') or 0}%",
            createdAt=created_at,
            cliExecuted=True,
            exportFormat="png" if preview_image else "glb",
            generatedAssets=generated_assets,
            diagnostics=list(task.get("diagnostics") or []),
        )

    @staticmethod
    def _build_project_history_id(project_name: str) -> str:
        normalized = re.sub(r"\s+", "-", project_name.strip())
        return normalized or "unnamed-project"

    @staticmethod
    def _build_version_id(task: dict[str, JSONValue]) -> str:
        version_id = str(task.get("versionId") or "").strip()
        if version_id:
            return version_id
        task_id = str(task.get("taskId") or "").strip()
        if task_id:
            suffix = task_id.split("_")[-1][-8:]
            return f"v_{suffix}"
        return f"v_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _extract_version_number(task: dict[str, JSONValue]) -> int:
        version_id = str(task.get("versionId") or "").strip()
        if version_id.startswith("v_"):
            return 1
        return 1

    @staticmethod
    def _resolve_preview_image(task: dict[str, JSONValue]) -> str | None:
        outputs = task.get("outputs")
        if not isinstance(outputs, dict):
            return None
        for key in (
            "renderPngAssetId",
            "drawingSvgAssetId",
            "enhancedImageAssetId",
        ):
            asset_id = IndustrialDesignWorkflowService._valid_asset_id(
                outputs.get(key)
            )
            if asset_id is not None:
                return IndustrialDesignWorkflowService._asset_url(asset_id)
        return None

    @staticmethod
    def _valid_asset_id(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return str(UUID(value))
        except ValueError:
            return None

    @classmethod
    def _first_output_asset_id(
        cls,
        outputs: dict[str, object],
    ) -> str | None:
        for key in (
            "renderPngAssetId",
            "drawingSvgAssetId",
            "modelOutputAssetId",
            "modelGlbAssetId",
            "modelStepAssetId",
            "explosionPngAssetId",
            "modelScriptAssetId",
        ):
            asset_id = cls._valid_asset_id(outputs.get(key))
            if asset_id is not None:
                return asset_id
        return None

    @classmethod
    def _collect_generated_assets(
        cls,
        outputs: dict[str, object],
    ) -> list[dict[str, object]]:
        mappings = (
            ("modelScriptAssetId", "script"),
            ("modelOutputAssetId", "cad"),
            ("drawingSvgAssetId", "svg"),
            ("drawingPdfAssetId", "pdf"),
            ("drawingDxfAssetId", "dxf"),
            ("renderPngAssetId", "image"),
            ("enhancedImageAssetId", "image"),
            ("explosionPngAssetId", "image"),
            ("modelGlbAssetId", "glb"),
            ("modelStepAssetId", "step"),
        )
        generated: list[dict[str, object]] = []
        seen: set[str] = set()
        for key, asset_type in mappings:
            asset_id = cls._valid_asset_id(outputs.get(key))
            if asset_id is None or asset_id in seen:
                continue
            seen.add(asset_id)
            generated.append(
                {
                    "assetId": asset_id,
                    "kind": asset_type,
                }
            )
        return generated

    def _build_forgecad_prompt(
        self,
        project_name: str,
        request: IndustrialDesignWorkflowRequest,
        design_spec: dict[str, JSONValue],
    ) -> str:
        lines = [
            f"项目名称：{project_name}",
            f"所属行业：{design_spec['industry']}",
            f"输入类型：{request.input_type}",
            request.text or "",
            *design_spec["assetSummaries"],
            "请生成可参数化调整的工业品/空间部件 ForgeCAD 脚本，并保留尺寸、装配关系和主要构件名称。",
        ]
        return "\n".join(item for item in lines if item)

    def _build_build123d_prompt(
        self,
        project_name: str,
        request: IndustrialDesignWorkflowRequest,
        design_spec: dict[str, JSONValue],
    ) -> str:
        user_desc = self._extract_user_description(request.text or "")
        lines = [
            f"项目名称：{project_name}",
            f"所属行业：{design_spec['industry']}",
            f"输入类型：{request.input_type}",
            user_desc or request.text or "",
            *design_spec["assetSummaries"],
            "请生成 build123d 代码，建模要求：结构完整、尺寸合理、可制造，单件模型即可（不需要装配体）。",
        ]
        return "\n".join(item for item in lines if item)

    @staticmethod
    def _extract_user_description(text: str) -> str:
        """从前端模板文本中提取用户的真实设计描述。

        前端发送的 text 格式：
          项目名称：...
          设计描述：用户真实描述
          生成 Prompt：模板拼接内容

        提取「设计描述」和「生成 Prompt」之间的内容，去掉前缀标记。
        """
        clean = text.strip()
        # 优先提取「设计描述：...」到下一个标记之间的内容
        match = re.search(
            r'设计描述[：:]\s*(.+?)(?=\n(?:生成\s*Prompt|负面约束|输入资产|上传意图|参考资产|$)|\n?$)',
            clean, re.DOTALL,
        )
        if match:
            desc = match.group(1).strip()
            if desc:
                return desc
        # 回退：如果 text 本身就很短（无模板结构），直接使用
        if len(clean) <= 200:
            return clean
        # 最后回退：去除已知的模板前缀行
        cleaned_lines = []
        for line in clean.split('\n'):
            stripped = line.strip()
            if any(stripped.startswith(prefix) for prefix in ('项目名称：', '所属行业：', '当前场景：', '输入资产：', '上传意图：', '生成 Prompt：', '生成 Prompt：', '参考资产：', '负面约束：')):
                continue
            if stripped:
                cleaned_lines.append(stripped)
        return '\n'.join(cleaned_lines) if cleaned_lines else clean

    @staticmethod
    def _product_common_sense_constraints(project_name: str, text: str) -> list[str]:
        source = f"{project_name} {text}".lower()
        constraints = [
            "PRODUCT COMMON SENSE — UNIVERSAL: The product must obey real gravity, real contact points, physically plausible scale, functional parts in believable positions, and matching contact shadows. No floating object, no surface clipping, no merged parts, no impossible intersections, no arbitrary decorative holes, no random components.",
        ]
        if any(keyword in source for keyword in ("手机支架", "手机架", "phone stand", "mobile phone stand")):
            constraints.extend([
                "PHONE STAND COMMON SENSE: the phone must rest on the cradle or front lip, with visible contact points.",
                "The phone must not intersect, clip through, float above, or unrealistically merge with the stand.",
                "Keep a realistic smartphone-to-stand size ratio, stable support angle, and physically plausible shadows.",
                "If the phone stand has a rabbit-shaped or cartoon back design, that is the back of the stand, not a separate object. The phone sits on the front cradle of the same stand body.",
                "The stand must have a stable base or clip mechanism, with a front retaining lip that holds the phone bottom edge. The phone leans back against the stand body at a 65-75 degree angle.",
            ])
        if any(keyword in source for keyword in ("沙发", "椅", "座椅", "凳", "sofa", "chair", "seat")):
            constraints.extend([
                "SEATING COMMON SENSE: seat cushions aligned on the frame, backrest connected to the seat, armrests attached on both sides when present, legs or base fully touching the floor.",
                "Upholstery thickness, seams, cushion compression, and stitching must be believable; no separated floating cushions, no melted soft parts, no unsupported backrest.",
            ])
        if any(keyword in source for keyword in ("桌", "茶几", "餐桌", "边几", "table", "desk")):
            constraints.extend([
                "TABLE COMMON SENSE: tabletop is level and has believable thickness; legs connect to the underside of the tabletop; all legs touch the floor.",
                "Objects on the tabletop must rest on the surface and never penetrate through it; table edges and corners must be consistent in perspective.",
            ])
        if any(keyword in source for keyword in ("柜", "衣柜", "书柜", "机柜", "控制柜", "cabinet", "wardrobe", "bookcase")):
            constraints.extend([
                "CABINET COMMON SENSE: doors drawers and shelves aligned to the cabinet grid, visible panel thickness, hinges and handles placed on usable edges.",
                "Cabinet stands vertically with a stable base; door gaps, drawer rails, ventilation slots, and service panels must be logically placed.",
            ])
        if any(keyword in source for keyword in ("灯", "台灯", "落地灯", "lamp", "light")):
            constraints.extend([
                "LAMP COMMON SENSE: light source located inside the lamp head or shade, stable base and visible support arm, cable or switch placed plausibly.",
                "Emitted light direction must match cast shadows; lamp head, arm, and base must be mechanically connected, not floating.",
            ])
        if any(keyword in source for keyword in ("电源", "储能", "充电", "电池", "设备", "充电宝", "power station", "battery", "charger")):
            constraints.extend([
                "ELECTRONIC DEVICE COMMON SENSE: ports aligned on the front panel, screen and buttons flush with the housing, handle attached to the main body.",
                "vents follow a consistent grid; cables plug into ports and never pass through the shell; housing thickness, bevels, seams, and rubber feet must be manufacturable.",
            ])
        if any(keyword in source for keyword in ("工业", "机械", "钣金", "装备", "industrial", "machine", "sheet metal")):
            constraints.extend([
                "INDUSTRIAL PRODUCT COMMON SENSE: bolts hinges vents access panels and reinforcement ribs placed logically; sheet metal bends and assembly seams visible.",
                "The structure must look manufacturable: consistent wall thickness, plausible fasteners, service access, stable mounting, and no random decorative mechanical noise.",
            ])
        return constraints

    def _build_image_prompt(
        self,
        project_name: str,
        request: IndustrialDesignWorkflowRequest,
        design_spec: dict[str, JSONValue],
    ) -> str:
        is_design_stage = request.options.generate_drawing and not request.options.generate_render
        is_propaganda_stage = request.options.generate_render
        user_desc = self._extract_user_description(request.text or "")
        is_furniture_scene = self._looks_like_furniture_scene(request.industry, project_name, user_desc)
        product_constraints = self._product_common_sense_constraints(project_name, request.text or "")

        # 如果用户已提供详细设计稿描述（含多视图/材质/尺寸），直接用做 prompt
        if user_desc and any(kw in user_desc for kw in ('设计稿','正视图','侧视图','顶视图','设计图','爆炸图','设计说明','三视图')):
            lines = [
                user_desc,
                "industrial design specification sheet, orthographic projection, multi-view arrangement, dimension annotation, material callout, clean line work, white background, technical presentation layout, studio lighting, high detail",
                "Negative: avoid blurry, deformed geometry, watermark, text artifacts, cluttered background.",
            ]
            return "\n".join(item for item in lines if item)

        if is_design_stage:
            if is_furniture_scene:
                lines = [
                    f"Home furniture design concept sheet for project '{project_name}'.",
                    f"Industry: {design_spec['industry']}.",
                    "Style: furniture product concept board, clean orthographic views with soft perspective support, clear silhouette, rounded forms, believable home-product proportions.",
                    "Quality: accurate furniture geometry, realistic wood, metal, paint or laminate material cues, clean layout, high detail, no unrelated machinery parts.",
                    "Lighting: bright studio ambient light, soft shadow, neutral white background, presentation-ready composition.",
                    "Output: 2D furniture design scheme focused on the product itself, with visible structure and styling details but without industrial machine blueprint noise.",
                ]
            else:
                lines = [
                    f"Industrial product design concept render for project '{project_name}'.",
                    f"Industry: {design_spec['industry']}.",
                    "Style: engineering drawing expression, orthographic view, clear structural zoning, technical presentation layout.",
                    "Quality: sharp edges, realistic material rendering, accurate proportions, clean composition, high detail.",
                    "Lighting: clean studio ambient light, soft directional shadow, no harsh contrast.",
                    "Output: 2D design scheme diagram with dimension logic, assembly relationship and material annotation.",
                ]
        elif is_propaganda_stage:
            if is_furniture_scene:
                lines = [
                    f"""DETAILED IMAGE GENERATION BRIEF: Create a commercial-grade home furniture advertising visual for project '{project_name}'. This is a text-to-image generation task. The output must look like a premium furniture catalog photograph, not a fantasy illustration, not an abstract concept, not a technical blueprint. The scene should feel like a real home environment with warm, inviting atmosphere.""",
                    f"""INDUSTRY CONTEXT: {design_spec['industry']}. Apply industry-appropriate visual conventions for furniture: warm lifestyle atmosphere, natural textures, realistic wood grain, soft fabric drape, comfortable proportions, precise joinery details. The setting should feel like a well-staged interior design photograph, not a sterile showroom.""",
                    """SUBJECT DESCRIPTION: The furniture product is the sole visual hero. Render it with its exact intended form, silhouette, structure, proportions, material finish, surface texture, color placement, decorative motif, functional seams, visible joinery, edge thickness, leg/base design, handle/hardware details, and any other identifiable design feature. The product must be a complete, coherent, fully-realized furniture piece, not a concept sketch, not a wireframe, not a partial assembly, not a technical drawing.""",
                    """PRODUCT STRUCTURE: Maintain the furniture product's structural integrity. Keep stable legs or base with solid ground contact, visible joinery (mortise and tenon, dovetail, dowel, or metal bracket as appropriate), functional doors/drawers/shelves with correct proportions, balanced weight distribution, believable wall thickness for the material (18-25mm for wood panels, 3-5mm for metal tubing), smooth edges with appropriate chamfer or radius, no twisted or warped geometry, no unsupported cantilevered spans, no merged or intersecting parts. Every functional element (hinges, handles, drawer slides, shelf supports, leveling feet) must be at the correct relative position and size.""",
                    """PHYSICAL PLAUSIBILITY — MANDATORY: The furniture must obey gravity and real-world physics. The product must sit solidly on a flat floor surface with all legs/base in full contact. Every contact point must have a matching contact shadow with correct density and direction — darker near the contact line, fading outward. The product must not float, hover, or clip through the floor. No impossible cantilever angles, no intersecting surfaces, no merged components. The shadow must be consistent with the direction of the key light source. For freestanding pieces: full base contact with the floor. For wall-mounted pieces: show the mounting interface or bracket against the wall. For multi-module pieces: show correct alignment and connection between modules.""",
                    """POSTER COMPOSITION: Hero product prominently displayed, three-quarter front angle showing the most visually informative view. Centered or slightly off-center layout (rule of thirds). Premium background with controlled depth gradient — a tastefully styled room corner, a minimalist lifestyle scene, or a clean studio cyclorama. Enough clean negative space (approximately 30% of the canvas) for future text overlay. Commercial furniture catalog hierarchy: product → scene → supporting decor → background. No clutter, no unnecessary decorative elements that compete with the product. Add 2-3 carefully selected complementary decor items (a small plant, a book, a cushion, a lamp) only if they enhance the product's context without blocking its silhouette.""",
                    """SCENE DESIGN: Create a warm, inviting home interior or a premium studio showroom scene. Use a matching interior style: modern minimal, Scandinavian, mid-century, industrial loft, or classic traditional, corresponding to the furniture's design language. The room should have subtle background elements: a wall with baseboard, a window with soft daylight, a floor (wood plank, tile, or neutral carpet) with appropriate texture. Color palette should complement the furniture — neutral walls, warm wood tones, subtle accent colors. The background must remain subordinate to the furniture: the product occupies the visual foreground. No distracting patterns, no busy wallpapers, no unrelated furniture or objects that dilute the hero product. The atmosphere should feel inviting and aspirational, like a real interior design photograph.""",
                    """MATERIAL AND SURFACE DETAIL: Render the furniture with its correct material properties. For solid wood: visible grain direction, natural pore texture, subtle color variation between heartwood and sapwood, appropriate sheen from the finish (matte, satin, or gloss). For wood veneer: continuous grain pattern, visible seam lines at panel edges, consistent color. For paint: smooth continuous finish, no orange peel, no brush strokes, no drips, correct sheen level. For metal hardware: appropriate reflectivity — brushed nickel has directional fine scratches, polished chrome has sharp mirror reflections, matte black has diffuse texture. For glass: edge thickness visible, subtle reflections at glancing angles, slight transparency. For fabric upholstery: visible weave pattern, soft folding and draping, no sharp creases, correct seam alignment, button tufting or stitching detail where applicable. For leather: natural grain texture, warm sheen, subtle stretch marks at seat corners. No waxy skin-like appearance, no dirty noise artifacts, no cartoon clay shading, no plastic toy look.""",
                    """LIGHTING — PRECISE SETUP: Natural-feeling interior lighting with a combination of ambient and directional light. Large soft window light from camera-left as the key light creating a soft 2:1 to 3:1 lighting ratio. Warm ambient fill from the room interior at 30-40% intensity to open up shadows. Subtle accent light from back-right or back-top to separate the furniture from the background. Warm color temperature (3500-4500K) for residential feel, slightly cooler (5000-5500K) for showroom or studio look. Soft, realistic contact shadow directly under the product with correct density gradient. For rooms with windows: visible window light direction, soft shadow falloff, subtle volumetric light beams if appropriate. No overexposed highlight clipping, no harsh direct flash look, no random colored light gels, no dramatic fashion-style lighting that distorts the product's natural appearance.""",
                    """CAMERA AND LENS: Full-frame architectural/product photography lens, 35-50mm focal length for furniture to capture the full piece without distortion while maintaining natural perspective. For larger pieces (sofas, beds, cabinets): 35-40mm. For smaller pieces (tables, chairs, sideboards): 50-70mm. Aperture setting f/8 to f/11 for sufficient depth of field to keep the entire furniture piece in sharp focus. Camera height at approximately 120-150cm from the floor (human eye level) for a natural room-view perspective. For tables: slightly elevated angle (10-15 degrees above horizontal) to show the tabletop surface. Sharp focus on the product with critical edge definition. No wide-angle distortion, no fisheye effect, no extreme tilt-shift. The camera-to-product distance should feel natural, approximately 2-3 meters for a sofa, 1.5-2 meters for a chair or table.""",
                    """GRAPHIC AND LAYOUT RULES: Leave clean negative space (approximately 30% of the canvas, typically in the upper or left area) reserved for future headline text, product name, tagline, and brand logo. Do NOT generate readable brand text, fake logos, QR codes, watermarks, random letters, incorrect annotations, unreadable typography, or messy graphic elements. If text appears unintentionally, it should be non-specific decorative shapes only. No barcode, no price tag, no certification marks, no size labels. The image should be a clean furniture hero visual ready for a graphic designer to add text overlay.""",
                    """QUALITY TARGET: Premium furniture catalog hero image quality. Photorealistic product appearance with correct geometry, lighting, and material. Warm, inviting atmosphere with high detail and sharp focus. Market-ready retail visual suitable for furniture catalog, ecommerce listing, design portfolio, or client presentation. Professional interior photography quality with studio-level lighting, precise shadow handling, and accurate color rendition. The final image should look like it was shot in a real home or a professional interior photography studio by an experienced architectural photographer, not generated by AI.""",
                    *product_constraints,
                    """NEGATIVE PROMPT — EXCLUDE: deformed geometry, twisted or warped frame, bloated proportions, shrunken parts, missing parts (legs, handles, doors), random extra parts, furniture floating above floor with no ground contact, missing contact shadow, shadow direction inconsistent with window light, duplicated product, extra furniture not described, unreadable text, watermark, fake logo, barcode, QR code, certification marks, cluttered messy background, busy patterns, over-stylized fantasy scene, cartoon style, illustration style, oil painting texture, watercolor effect, sketch lines, grainy noise, chromatic aberration, lens flare artifacts, motion blur, double exposure, HDR overprocessing, oversaturated colors, color cast, skin-like texture on wood, clay-like shading, plastic toy appearance, cheap furniture look, DIY construction appearance, damaged or worn furniture, construction site background, warehouse background, exterior outdoor scene.""",
                ]
            else:
                lines = [
                    f"""DETAILED IMAGE GENERATION BRIEF: Create a commercial-grade industrial product advertising visual for project '{project_name}'. This is a text-to-image generation task. The output must look like a premium product marketing photograph, not a fantasy illustration, not an abstract concept, not a technical blueprint. The scene should feel like a professional product photography studio with clean, precise visual presentation.""",
                    f"""INDUSTRY CONTEXT: {design_spec['industry']}. Apply industry-appropriate visual conventions. For consumer electronics: clean tech aesthetic, matte materials, precise edges, subtle reflections. For industrial equipment: robust engineering look, metallic surfaces, technical precision, mechanical detailing. For medical devices: clean white aesthetic, soft lighting, ergonomic shapes. For automotive parts: high-gloss painted surfaces, precise machined edges, mechanical complexity.""",
                    """SUBJECT DESCRIPTION: The industrial product is the sole visual hero. Render it with its exact intended form, silhouette, structure, proportions, material finish, surface texture, color placement, functional seams, visible edge thickness, support feet, joints, mounting holes, vents, buttons, ports, indicators, heat sinks, fasteners, and any other identifiable design feature. The product must be a complete, coherent, fully-realized industrial product, not a concept sketch, not a wireframe, not a partial assembly, not a technical drawing.""",
                    """PRODUCT STRUCTURE: Maintain the product's structural integrity. Keep a stable base, visible support surface, rounded safe edges, manufacturable wall thickness, believable injection-molded plastic, die-cast metal, machined aluminum, or silicone material finish. Clean parting lines, no melted surface, no twisted or warped geometry, no random extra parts, no bloated or shrunken proportions. For assembled products: show correct alignment between components, consistent gap tolerances, believable fastener placement (screws, bolts, snap-fits). Every functional element (buttons, vents, indicators, ports, hinges, joints, heat sinks, mounting brackets) must be at the correct relative position and size. The product should look like it could be manufactured, not like a free-form sculpture.""",
                    """PHYSICAL PLAUSIBILITY — MANDATORY: All objects must obey gravity and real-world physics. Every object must sit on a flat surface or in a mechanically believable mounting position. Every contact point must have a matching contact shadow with correct density and direction — darker near the contact line, fading outward. The product must not float, hover, or clip through the surface. Avoid impossible cantilever angles, intersecting objects, merged surfaces, or objects that lack a visible support structure. The shadow must be consistent with the direction of the key light source. For wall-mounted products: show the mounting interface or bracket. For tabletop products: show the full base contact with the surface. For handheld products: show them resting on a surface or in a stand, not floating in mid-air.""",
                    *product_constraints,
                    """POSTER COMPOSITION: Hero product prominently displayed, three-quarter front angle is the default (choose the angle that best shows the product's key features and functional surfaces). Centered or slightly off-center layout (rule of thirds). Premium background with controlled depth gradient — a clean studio cyclorama, a subtle gradient backdrop, or a minimal tech environment. Enough clean negative space (approximately 40% of the canvas) reserved for future headline text, technical specifications, selling points, and brand logo. Commercial product marketing hierarchy: product → key features → supporting visuals → background. No clutter, no unnecessary decorative elements that compete with the product.""",
                    """SCENE DESIGN: Use a modern desk, minimal tech environment, or clean studio cyclorama scene. For consumer products: place the product on a clean tabletop or architectural surface (concrete, wood, acrylic, marble, brushed metal) that complements the product's color and material. For industrial products: use a neutral workshop, lab bench, or technical background. Add only props that directly support the product story (e.g., a phone on a phone stand, a cable on a charger, a tool next to a device) and do not block the product silhouette. Keep the background subordinate to the product — the product should occupy the visual foreground. No distracting background patterns, no busy textures, no unrelated objects.""",
                    """MATERIAL AND SURFACE DETAIL: Render the product with its correct material properties. For ABS plastic: subtle matte micro-texture, soft specular highlights, no waxy or greasy sheen, clean bevels at edges. For silicone or rubber: soft matte finish, slight surface grip texture, no reflections. For metal: appropriate reflectivity — brushed aluminum has directional anisotropic reflections, polished steel has sharp mirror-like reflections, anodized surfaces have diffuse colored reflections, machined surfaces have visible tool marks. For glass: transparency, edge refraction, subtle reflections at glancing angles. For painted surfaces: smooth continuous finish, no orange peel, no brush strokes, correct sheen (matte/satin/gloss). For carbon fiber: visible weave pattern, directional light reflection. No waxy skin-like appearance, no dirty noise artifacts, no cartoon clay shading, no plastic toy look unless explicitly requested as a style.""",
                    """LIGHTING — PRECISE SETUP: Large softbox as the key light positioned at upper front-left 45-degree angle, creating a natural 3:1 lighting ratio (key light 3x brighter than fill). Soft fill light from front-right at 30% intensity to open up shadows. Subtle rim light or edge light from back-right or back-top to separate the product from the background and highlight the product silhouette. Controlled reflections on glossy surfaces (reflection diffusers or bounce cards implied). Soft, realistic contact shadow directly under the product with correct density gradient — darker near the contact line, fading outward. No overexposed highlight clipping, no harsh direct flash look, no random neon glow colors unless the product has built-in LEDs. For products with screens: show the screen as dark glass with subtle reflections, not a bright glowing display that competes with the product. For products with indicators: show subtle LED glow, not overpowering light sources.""",
                    """CAMERA AND LENS: Full-frame product photography lens, 70-85mm focal length for natural perspective compression without distortion. Aperture setting f/4 to f/5.6 for sufficient depth of field to keep the entire product in sharp focus. Eye-level to slightly elevated viewpoint (15-25 degrees above horizontal) for a natural product catalog look. Sharp focus on the product with critical edge definition. Background may be softly defocused (bokeh) only if it does not blur the product outline. No wide-angle distortion, no fisheye effect, no extreme macro perspective. The camera-to-product distance should feel natural, approximately 1-1.5 meters for a typical product.""",
                    """GRAPHIC AND LAYOUT RULES: Leave clean blank area (approximately 40% of the canvas, typically in the upper or left area) reserved for future headline text, technical specifications, selling points, and brand logo. Do NOT generate readable brand text, fake logos, QR codes, watermarks, random letters, incorrect annotations, unreadable typography, or messy graphic elements. If text appears unintentionally, it should be non-specific decorative shapes only. No barcode, no price tag, no certification marks. The image should be a clean product hero visual ready for a graphic designer to add text overlay.""",
                    """QUALITY TARGET: Premium ecommerce hero image quality. Realistic physical product appearance with correct geometry, lighting, and material. Clean composition with high detail and sharp focus. Market-ready advertising visual suitable for Amazon listing, company catalog, trade show display, or client presentation. Professional product photography quality with studio-level lighting, precise shadow handling, and accurate color rendition. The final image should look like it was shot in a professional photo studio by an experienced product photographer, not generated by AI.""",
                    *product_constraints,
                    """NEGATIVE PROMPT — EXCLUDE: deformed geometry, twisted or warped body, melted plastic, bloated proportions, shrunken parts, missing parts, random extra parts, unnatural holes, phone clipping through or penetrating the stand surface, floating product with no ground contact, impossible cantilever, missing contact shadow, shadow direction inconsistent with key light, duplicated product, extra objects not in the prompt, unreadable text, watermark, fake logo, barcode, QR code, certification marks, messy background, over-stylized fantasy scene, cartoon style, illustration style, oil painting texture, watercolor effect, sketch lines, grainy noise, chromatic aberration, lens flare artifacts, motion blur, double exposure, HDR overprocessing, oversaturated colors, color cast, skin-like texture on non-organic products, clay-like shading, plastic toy appearance, cheap product look, DIY prototype appearance, damaged or worn product, construction site background, warehouse background.""",
                ]
        else:
            lines = [
                f"Industrial product concept visualization for project '{project_name}'.",
                f"Industry: {design_spec['industry']}.",
                "Style: technical concept render, engineering clarity, structural detail visible.",
                "Quality: sharp focus, realistic material, clean background, no watermark.",
            ]

        # 提取用户真实描述并追加（而非整段前端模板文本）
        if user_desc:
            lines.append(f"Design requirement: {user_desc}")
        if design_spec["assetSummaries"]:
            lines.append("Reference assets:")
            lines.extend(design_spec["assetSummaries"])

        # 负面约束（借鉴 Stable Diffusion / Midjourney 负面提示词技巧）
        if is_furniture_scene:
            lines.append(
                "Negative: avoid industrial machinery, motors, control cabinets, mechanical assembly internals, "
                "blueprint overlay noise, watermark, wrong text, cluttered background, excessive artistic stylization."
            )
        else:
            lines.append(
                "Negative: avoid blurry, deformed geometry, watermark, text artifacts, "
                "excessive artistic stylization, cluttered background, poster-like composition."
            )
        return "\n".join(item for item in lines if item)

    @staticmethod
    def _looks_like_furniture_scene(industry: str | None, project_name: str, user_desc: str) -> bool:
        combined = " ".join(filter(None, [industry or "", project_name, user_desc]))
        return any(
            keyword in combined
            for keyword in (
                "家居", "家具", "茶几", "桌", "桌几", "边几", "餐桌", "咖啡桌", "柜", "椅", "沙发", "床", "玄关",
            )
        )

    async def _generate_external_design_image(
        self,
        *,
        prompt: str,
        images: list[str] | None = None,
        optimize_prompt: bool = True,
        image_model: str | None = None,
        image_provider: str | None = None,
    ) -> dict[str, JSONValue]:
        if (image_model and image_model.lower() != "auto") or image_provider:
            return await self.ai_model_gateway.generate_design_image(
                prompt=prompt,
                images=images,
                optimize_prompt=optimize_prompt,
                model=image_model,
                provider=image_provider,
            )
        return await self.ai_model_gateway.generate_design_image(
            prompt=prompt,
            images=images,
            optimize_prompt=optimize_prompt,
            provider="nodapi",
        )

    def _image_provider_label(self) -> str:
        return "NodAPI"

    async def _try_generate_enhanced_image(
        self,
        *,
        request: IndustrialDesignWorkflowRequest,
        project_name: str,
        design_spec: dict[str, JSONValue],
        diagnostics: list[dict[str, str]],
        user_id: str,
        db: Session | None = None,
        task_id: str | None = None,
        publish_assets: bool = True,
    ) -> dict[str, str] | None:
        """按工作流选项在后端直接调用临时图片精修能力。"""
        if not request.options.enhance_image:
            return None
        image_asset_id = self._resolve_first_uploaded_image_asset_id(request)
        if image_asset_id is None:
            diagnostics.append({
                "level": "error",
                "title": "宣发图缺少参考图",
                "detail": "宣发阶段已固定为图片编辑，必须选择一张已入库的设计图作为参考。",
            })
            return None
        if not self.image2_edit_service.configured():
            diagnostics.append({
                "level": "error",
                "title": "图片编辑未启用",
                "detail": "宣发阶段已固定为图片编辑，但 Image2 Edit 脚本未配置或不可执行。",
            })
            return None

        async def edit(active_db: Session) -> dict[str, object]:
            return await self.image2_edit_service.edit_and_store(
                db=active_db,
                user_id=user_id,
                request=IndustrialDesignImageEditRequest(
                    prompt=self._build_enhance_image_prompt(project_name, request, design_spec),
                    imagePaths=["database-asset"],
                    size="1536x1024",
                    quality="medium",
                    outputFormat="png",
                    inputFidelity="high",
                ),
                image_asset_ids=[image_asset_id],
                task_id=task_id,
                publish_assets=publish_assets,
            )

        try:
            if db is not None:
                result = await edit(db)
            else:
                with self.db_context_factory() as active_db:
                    result = await edit(active_db)
        except Image2EditServiceError as exc:
            logger.warning(
                "图片精修失败，error_code=%s",
                exc.error_code,
                exc_info=True,
            )
            diagnostics.append({
                "level": "error",
                "title": "图片编辑失败",
                "detail": f"宣发图必须基于参考设计图编辑生成，本次图片编辑失败：{exc.message}",
            })
            return None

        output_asset_id = result.get("outputAssetId")
        download_url = result.get("downloadUrl")
        if not isinstance(output_asset_id, str) or not isinstance(download_url, str):
            diagnostics.append({
                "level": "error",
                "title": "图片编辑失败",
                "detail": "图片编辑结果缺少数据库资产标识。",
            })
            return None
        response_asset_id = result.get("responseAssetId")
        response = {"assetId": output_asset_id, "url": download_url}
        if isinstance(response_asset_id, str):
            response["responseAssetId"] = response_asset_id
        return response

    @staticmethod
    def _image_edit_mode(request: IndustrialDesignWorkflowRequest) -> str:
        raw = request.context.get("imageEditMode")
        mode = str(raw).strip().lower() if raw is not None else ""
        aliases = {
            "poster": "poster",
            "promotion": "poster",
            "render": "poster",
            "scene": "scene_fusion",
            "scene_fusion": "scene_fusion",
            "fusion": "scene_fusion",
            "exploded": "exploded",
            "explosion": "exploded",
            "explode": "exploded",
            "fake_3d": "fake_3d",
            "faux_3d": "fake_3d",
            "3d": "fake_3d",
            "plan_2d": "plan_2d",
            "2d_plan": "plan_2d",
            "2d": "plan_2d",
            "orthographic": "plan_2d",
        }
        if mode in aliases:
            return aliases[mode]
        if request.options.generate_explosion:
            return "exploded"
        return "poster"

    @classmethod
    def _image_edit_output_kind(
        cls,
        request: IndustrialDesignWorkflowRequest,
    ) -> str:
        return "explosion" if cls._image_edit_mode(request) == "exploded" else "render"

    @staticmethod
    def _apply_image_edit_result(
        outputs: dict[str, JSONValue],
        image_edit_result: dict[str, str],
        *,
        output_kind: str = "render",
    ) -> None:
        if output_kind == "explosion":
            outputs["explosionPngAssetId"] = image_edit_result["assetId"]
            outputs["explosionPng"] = image_edit_result["url"]
            if image_edit_result.get("responseAssetId"):
                outputs["explosionResponseAssetId"] = image_edit_result[
                    "responseAssetId"
                ]
        else:
            outputs["enhancedImageAssetId"] = image_edit_result["assetId"]
            outputs["enhancedImage"] = image_edit_result["url"]
            if image_edit_result.get("responseAssetId"):
                outputs["enhancedImageResponseAssetId"] = image_edit_result[
                    "responseAssetId"
                ]
            outputs["renderPngAssetId"] = image_edit_result["assetId"]
            outputs["renderPng"] = image_edit_result["url"]
        outputs["imageProvider"] = "image2-edit"

    @staticmethod
    def _resolve_first_uploaded_image_asset_id(
        request: IndustrialDesignWorkflowRequest,
    ) -> str | None:
        image_extensions = {"png", "jpg", "jpeg", "webp"}
        for item in request.asset_metas:
            extension = item.extension.lower().lstrip(".")
            if extension in image_extensions:
                return item.asset_id
        return None

    async def _resolve_reference_image_urls(
        self,
        urls: list[str],
        user_id: str,
    ) -> list[str]:
        """把相对资产 URL（/api/...）转成可直接下载的 data: URI，供远程 ComfyUI 图生图使用。"""
        resolved: list[str] = []
        for url in urls:
            if not url or url.startswith("data:"):
                resolved.append(url)
                continue
            if url.startswith("/"):
                asset_id = self._extract_asset_id_from_url(url)
                if asset_id is None:
                    continue
                try:
                    with self.db_context_factory() as db:
                        content = self.asset_service.read_bytes(db, asset_id, user_id)
                except Exception:
                    logger.warning("参考图资产读取失败 asset_id=%s", asset_id, exc_info=True)
                    continue
                import base64

                mime = "image/png"
                encoded = base64.b64encode(content).decode("ascii")
                resolved.append(f"data:{mime};base64,{encoded}")
                continue
            resolved.append(url)
        return resolved

    @staticmethod
    def _extract_asset_id_from_url(url: str) -> UUID | None:
        import re as _re

        match = _re.search(r"/assets/([0-9a-fA-F-]{36})/download", url)
        if match:
            try:
                return UUID(match.group(1))
            except ValueError:
                return None
        return None

    def _build_explosion_image_prompt(
        self,
        project_name: str,
        request: IndustrialDesignWorkflowRequest,
        design_spec: dict[str, JSONValue],
    ) -> str:
        user_desc = self._extract_user_description(request.text or "")
        has_reference = bool(request.asset_urls or request.asset_metas)
        lines = [
            f"2D exploded assembly diagram for product project '{project_name}'.",
            f"Industry: {design_spec['industry']}.",
            "Style: flat technical exploded-view diagram, components separated along vertical axis showing assembly order, clean white background.",
            "Quality: crisp engineering illustration, parts labeled by layout position, consistent perspective, high detail, no photo-realistic shading.",
            "Composition: the whole product shown as an exploded assembly with each part floating apart in order, clear spatial relationship, suitable for manufacturing reference.",
        ]
        if has_reference:
            lines.append("Keep the exact product form, structure, proportions and material cues from the reference product image; do not redesign a different product. Break it into its constituent parts in an exploded assembly view.")
        if user_desc:
            lines.append(f"User intent: {user_desc}")
        return "\n".join(item for item in lines if item)

    def _build_enhance_image_prompt(
        self,
        project_name: str,
        request: IndustrialDesignWorkflowRequest,
        design_spec: dict[str, JSONValue],
    ) -> str:
        user_desc = self._extract_user_description(request.text or "")
        product_constraints = self._product_common_sense_constraints(project_name, request.text or "")
        image_edit_mode = self._image_edit_mode(request)
        mode_briefs = {
            "poster": f"""POSTER IMAGE EDITING BRIEF: Create a realistic commercial advertising poster for project '{project_name}'. This is an image-to-image editing task: take the uploaded reference product image as the identity source and composite it into a premium ecommerce advertising poster. The output must look like a realistic product marketing visual, not fantasy art, not illustration, not abstract concept.""",
            "scene_fusion": f"""SCENE FUSION IMAGE EDITING BRIEF: Create a realistic scene-fusion product image for project '{project_name}'. This is a strict image-to-image editing task. Extract the exact product from the reference image, preserve its identity, and place the same product into a believable real-world usage scene. The scene may change; the product must not be redesigned.""",
            "exploded": f"""EXPLODED ASSEMBLY IMAGE EDITING BRIEF: Create a flat 2D exploded assembly diagram for project '{project_name}'. This is a strict image-to-image editing task. Use the reference image as the only source of the product's geometry, proportions, materials, and part relationships. Decompose the same product into visible functional layers and components, with clean separation, connector guide lines, and assembly order. Do not invent a new product.""",
            "fake_3d": f"""FAUX 3D IMAGE EDITING BRIEF: Create a 2D faux-3D product render for project '{project_name}'. This is a strict image-to-image editing task. Preserve the exact product identity from the reference image, then present it in an isometric or three-quarter product-render angle with controlled industrial lighting. This is not real 3D mesh generation and not text-to-image redesign.""",
            "plan_2d": f"""2D ORTHOGRAPHIC ENGINEERING DRAWING BRIEF: Create a flat 2D multi-view engineering drawing sheet for project '{project_name}'. This is a strict image-to-image editing task. Use the uploaded reference image as the only source of the product identity, silhouette, proportions, visible structure, materials, and feature placement. Convert the same object into clean orthographic views with dimensions. Do not redesign, do not invent a different product, and do not create a lifestyle scene.""",
        }
        mode_rules = {
            "poster": """MODE-SPECIFIC GOAL: Build a poster-ready hero image. Keep the same product as the reference, create a premium advertising composition, and reserve clean blank space for future graphic text.""",
            "scene_fusion": """MODE-SPECIFIC GOAL: Composite the same product into a realistic usage environment. Match perspective, scale, surface contact, lighting direction, color temperature, and shadow softness between the extracted product and the new scene. The product must look physically present in the scene, not pasted on top.""",
            "exploded": """MODE-SPECIFIC GOAL: Convert the reference product into a clear 2D exploded technical diagram. Separate the original product into plausible visible parts: outer shell, panels, drawers, doors, supports, fasteners, brackets, internal layers, functional modules, gaskets, hinges, rails, shelves, electronic modules, or other category-appropriate components. Keep every component derived from the original silhouette and structure. Use orthographic or isometric technical layout, white background, thin connector lines, small numbered callout circles, clean spacing, and no photorealistic lifestyle background.""",
            "fake_3d": """MODE-SPECIFIC GOAL: Re-render the reference product as a single coherent 2D image that only imitates 3D depth through perspective, shadow, ambient occlusion, bevel highlights, and material shading. Keep the same recognizable design, colors, part positions, and proportions from the reference. Do not create CAD, STEP, STL, GLB, wireframe mesh, or a different product.""",
            "plan_2d": """MODE-SPECIFIC GOAL: Convert the same reference product into a 2D orthographic multi-view drawing sheet. Show front view, rear view, left side view, right side view, top view, and bottom view of the same object, all aligned to a consistent scale. Add dimension lines, centerlines, visible/hidden edges, material callouts, structure labels, and two small detail insets if useful. The drawing must show only this one product object on a clean white technical drafting background.""",
        }
        if image_edit_mode == "plan_2d":
            lines = [
                mode_briefs["plan_2d"],
                mode_rules["plan_2d"],
                f"""INDUSTRY CONTEXT: {design_spec['industry']}. Use category-appropriate engineering drafting conventions and realistic dimensions.""",
                """SUBJECT LOCK — CRITICAL: the reference image is the sole identity source. Preserve the original product silhouette, proportions, visible structure, material separation, color placement, handles, ports, hinges, panels, shelves, supports, wheels, frame tubes, fasteners, decorative motifs, and all recognizable design features. Do NOT redesign the product. Do NOT simplify it into a generic object. Do NOT create a new variant.""",
                """ORTHOGRAPHIC VIEW REQUIREMENT: output a flat 2D technical sheet containing front view, rear view, left side view, right side view, top view, and bottom view of the exact same object. Every view must correspond to the same product geometry. Views must be aligned on a clean grid with consistent scale and shared centerlines. Include dimension chains for overall length, width, height, depth, key hole spacing, shelf spacing, tube diameter, panel thickness, wheel diameter, or other category-relevant dimensions.""",
                """DRAWING STYLE: crisp black and dark-gray vector-like linework on a white drafting background, thin dimension lines, arrowheads, dashed hidden edges, center marks, small callout bubbles, restrained material labels. Use subtle grayscale fills only to separate materials if necessary. No photorealistic shadows, no lifestyle scene, no poster layout, no decorative background, no perspective camera, no 3D render.""" ,
                """PRODUCT CONSISTENCY: if the source is a product render or poster, infer the missing orthographic faces from the visible product while keeping the same identity. If the source already contains a multi-view design sheet, preserve the same object and cleanly redraw the views; do not treat each view as a separate product. If any face is ambiguous, infer conservatively from the reference instead of inventing decorative parts.""",
                *product_constraints,
                """NEGATIVE PROMPT — EXCLUDE: different product, redesigned product, generic replacement object, multiple unrelated variants, lifestyle background, room scene, desk scene, human model, poster text, marketing headline, photorealistic perspective render, 3D software viewport, exploded parts, random holes, inconsistent views, mismatched front and side view, impossible dimensions, warped geometry, unreadable fake text, watermark, logo, QR code, barcode, messy annotations.""",
            ]
            if user_desc:
                lines.append(f"""USER INTENT: {user_desc}. Use this only to understand the product category and drafting emphasis; do not override the reference image identity.""")
            return "\n".join(item for item in lines if item)
        lines = [
            mode_briefs.get(image_edit_mode, mode_briefs["poster"]),
            mode_rules.get(image_edit_mode, mode_rules["poster"]),
            f"""INDUSTRY CONTEXT: {design_spec['industry']}. Apply industry-appropriate visual conventions. For consumer electronics: clean tech aesthetic, matte materials, precise edges. For furniture: warm lifestyle, natural textures, realistic wood grain. For industrial equipment: robust engineering look, metallic surfaces, technical precision.""",
            """SUBJECT LOCK — CRITICAL: the reference image is the sole identity source. Preserve every recognizable design feature of the reference product: exact silhouette, outer profile, structural proportions, color placement, material finish, surface texture, decorative motif, functional seams, visible edge thickness, support feet, joints, mounting holes, vents, buttons, ports, logo position, and any other identifiable detail. Do NOT redesign the product. Do NOT invent a different product. Do NOT create a new variant. Do NOT change the main geometry. The only allowed changes are mode-specific presentation changes: camera angle, background environment, lighting setup, exploded spacing, poster composition layout, or scene composition.""",
            """REFERENCE IMAGE USAGE: Cut out or isolate the original product subject from the reference image. If the reference is a design specification sheet, multi-view orthographic board, or technical drawing, identify the main product form (the assembled product, not individual views) and reconstruct the same product as one coherent 3D object. Keep reference fidelity at maximum. The reference image controls the product identity; the editing prompt controls the environment, camera, lighting, poster layout, and scene composition. Do not let the editing prompt override the product identity from the reference.""",
            """PRODUCT STRUCTURE: Maintain the original product's structural integrity. Keep a stable base, visible support surface, front retaining lip or cradle groove when applicable, rounded safe edges, manufacturable wall thickness, believable injection-molded plastic, die-cast metal, or silicone material finish. Clean parting lines, no melted surface, no twisted or warped geometry, no random extra parts, no bloated or shrunken proportions. Every functional element (buttons, vents, indicators, ports, hinges, joints) must be at the correct relative position and size.""",
            """PHYSICAL PLAUSIBILITY — MANDATORY: All objects must obey gravity and real-world physics. Every object must sit on a flat surface or in a mechanically believable mounting position. Every contact point must have a matching contact shadow with correct density and direction. The product must not float, hover, or clip through the surface. Avoid impossible cantilever angles, intersecting objects, merged surfaces, or objects that lack a visible support structure. The shadow must be consistent with the direction of the key light source. For wall-mounted products, show the mounting interface or bracket. For tabletop products, show the full base contact with the surface.""",
            *product_constraints,
            """POSTER COMPOSITION: Hero product prominently displayed, three-quarter front angle is the default (choose the angle that best shows the product's key features). Centered or slightly off-center layout (rule of thirds). Premium background with controlled depth gradient. Enough clean negative space (approximately 40% of the canvas) reserved for future headline text, selling points, and brand logo. Commercial ecommerce poster hierarchy: product → headline area → supporting visuals → background. No clutter, no unnecessary decorative elements that compete with the product.""",
            """SCENE DESIGN: Use a modern desk, minimal lifestyle environment, or clean studio cyclorama scene. For consumer products: place the product on a clean tabletop or architectural surface (concrete, wood, acrylic, marble) that complements the product's color and material. For industrial products: use a neutral workshop or technical background. Add only props that directly support the product story (e.g., a phone on a phone stand, a cable on a charger) and do not block the product silhouette. Keep the background subordinate to the product — the product should occupy the visual foreground. No distracting background patterns, no busy textures, no unrelated objects.""",
            """MATERIAL AND SURFACE DETAIL: Render the product with its correct material properties. For ABS plastic: subtle matte micro-texture, soft specular highlights, no waxy or greasy sheen, clean bevels at edges. For silicone or rubber: soft matte finish, slight surface grip texture, no reflections. For metal: appropriate reflectivity — brushed aluminum has directional anisotropic reflections, polished steel has sharp mirror-like reflections, anodized surfaces have diffuse colored reflections. For glass: transparency, edge refraction, subtle reflections at glancing angles. For painted surfaces: smooth continuous finish, no orange peel, no brush strokes. For wood: visible grain direction, subtle pore texture, natural color variation. For fabric: visible weave pattern, soft folding, no sharp creases. No waxy skin-like appearance, no dirty noise artifacts, no cartoon clay shading, no plastic toy look unless explicitly requested as a style.""",
            """LIGHTING — PRECISE SETUP: Large softbox as the key light positioned at upper front-left 45-degree angle, creating a natural 3:1 lighting ratio (key light 3x brighter than fill). Soft fill light from front-right at 30% intensity to open up shadows. Subtle rim light or edge light from back-right or back-top to separate the product from the background. Controlled reflections on glossy surfaces (reflection diffusers or bounce cards implied). Soft, realistic contact shadow directly under the product with correct density gradient — darker near the contact line, fading outward. No overexposed highlight clipping, no harsh direct flash look, no random neon glow colors unless the product has built-in LEDs. For products with screens: show the screen as dark glass with subtle reflections, not a bright glowing display that competes with the product.""",
            """CAMERA AND LENS: Full-frame product photography lens, 70-85mm focal length for natural perspective compression without distortion. Aperture setting f/4 to f/5.6 for sufficient depth of field to keep the entire product in sharp focus. Eye-level to slightly elevated viewpoint (15-25 degrees above horizontal) for a natural product catalog look. Sharp focus on the product with critical edge definition. Background may be softly defocused (bokeh) only if it does not blur the product outline. No wide-angle distortion, no fisheye effect, no extreme macro perspective. The camera-to-product distance should feel natural, approximately 1-1.5 meters for a typical product.""",
            """GRAPHIC AND LAYOUT RULES: Leave clean blank area (approximately 40% of the canvas, typically in the upper or left area) reserved for future headline text, selling points, and brand logo. Do NOT generate readable brand text, fake logos, QR codes, watermarks, random letters, incorrect annotations, unreadable typography, or messy graphic elements. If text appears unintentionally, it should be non-specific decorative shapes only. No barcode, no price tag, no certification marks. The poster should be a clean product hero image ready for a graphic designer to add text overlay.""",
            """QUALITY TARGET: Premium ecommerce hero image quality. Realistic physical product appearance with correct geometry, lighting, and material. Clean composition with high detail and sharp focus. Market-ready advertising visual suitable for Amazon listing, Shopify storefront, or brand catalog. Professional product photography quality with studio-level lighting, precise shadow handling, and accurate color rendition. The final image should look like it was shot in a professional photo studio, not generated by AI.""",
            *product_constraints,
            """NEGATIVE PROMPT — EXCLUDE: wrong product design, redesigned product, different product variant, deformed geometry, twisted or warped body, melted plastic, bloated proportions, shrunken parts, missing parts, random extra parts, unnatural holes, phone clipping through or penetrating the stand surface, floating product with no ground contact, impossible cantilever, missing contact shadow, shadow direction inconsistent with key light, duplicated product, extra objects not in the prompt, unreadable text, watermark, fake logo, barcode, QR code, certification marks, messy background, over-stylized fantasy scene, cartoon style, illustration style, oil painting texture, watercolor effect, sketch lines, grainy noise, chromatic aberration, lens flare artifacts, motion blur, double exposure, HDR overprocessing, oversaturated colors, color cast, skin-like texture on non-organic products, clay-like shading, plastic toy appearance.""",
        ]
        if user_desc:
            lines.append(f"""USER INTENT: {user_desc}. Incorporate this as the primary product description and design intent. This describes the product to be featured in the poster. Do not override the reference image's product identity, but use this text to understand the product category, target audience, and key selling points.""")
        return "\n".join(item for item in lines if item)

    def _build_zoo_prompt(
        self,
        project_name: str,
        request: IndustrialDesignWorkflowRequest,
        design_spec: dict[str, JSONValue],
    ) -> str:
        lines = [
            f"Create a manufacturable industrial product concept model for project '{project_name}'.",
            f"Industry: {design_spec['industry']}.",
            "Return a clean 3D CAD concept with clear main structure, assembly relationship, proportional dimensions, and no decorative noise.",
            "The model should be suitable for design preview and early engineering discussion.",
        ]
        if request.text:
            lines.append(request.text)
        if design_spec["assetSummaries"]:
            lines.append("Reference assets:")
            lines.extend(design_spec["assetSummaries"])
        return "\n".join(item for item in lines if item)

    def _build_default_drawing_request(self, project_name: str, industry: str | None) -> WardrobeDrawingRequest:
        normalized_industry = industry if industry in {"家居智造", "装备制造", "医疗器械", "汽车零部件"} else "家居智造"
        return WardrobeDrawingRequest.model_validate({
            "industry": normalized_industry,
            "templateType": "wardrobe" if normalized_industry == "家居智造" else "control_cabinet",
            "projectName": project_name,
            "width": 3600,
            "height": 2600,
            "depth": 600,
            "doorType": "sliding_mixed",
            "material": "板材 18mm / 金属连接件",
            "modules": [
                {"sectionType": "hanging", "width": 600, "label": "定制柜体"},
                {"sectionType": "drawer_shelf", "width": 1800, "drawerCount": 2, "shelfCount": 4, "label": "主体收纳"},
                {"sectionType": "shelf", "width": 1200, "shelfCount": 5, "label": "开放格/工作区"},
            ],
        })

    def _build_three_preview_spec(self, project_name: str, request: IndustrialDesignWorkflowRequest) -> dict[str, JSONValue]:
        return {
            "type": "parametric_room_or_product_preview",
            "projectName": project_name,
            "sourceMode": request.input_type,
            "engine": "threejs",
            "layout": {
                "width": 3600,
                "height": 2600,
                "depth": 600,
                "modules": ["定制柜体", "主体收纳", "开放格/工作区"],
            },
        }

    def persist_zoo_outputs(
        self,
        *,
        db: Session,
        user_id: str,
        project_name: str,
        outputs: dict[str, object],
        task_id: str | None = None,
        publish_assets: bool = True,
    ) -> dict[str, str]:
        """将 Zoo 返回的 CAD 文件写入数据库，返回按格式索引的资产 ID。"""
        saved: dict[str, str] = {}
        project_slug = self._slugify(project_name)

        for raw_name, raw_value in outputs.items():
            if not isinstance(raw_name, str) or not isinstance(raw_value, str):
                continue
            suffix = Path(raw_name).suffix.lower()
            if suffix not in {".glb", ".step", ".stp"}:
                continue
            try:
                output_bytes = b64decode(raw_value, validate=True)
            except (ValueError, Base64DecodeError) as exc:
                raise ValueError("Zoo CAD output is invalid base64") from exc
            if len(output_bytes) > self.max_generated_asset_size_bytes:
                raise ValueError("Zoo CAD output exceeds size limit")
            final_suffix = ".step" if suffix == ".stp" else suffix
            kind = "glb" if final_suffix == ".glb" else "step"
            if kind == "glb":
                valid = (
                    len(output_bytes) >= 12
                    and output_bytes[:4] == b"glTF"
                    and int.from_bytes(output_bytes[4:8], "little") == 2
                    and int.from_bytes(output_bytes[8:12], "little")
                    == len(output_bytes)
                )
            else:
                normalized = output_bytes.lstrip().upper()
                valid = normalized.startswith(b"ISO-10303-21;") and (
                    b"END-ISO-10303-21;" in normalized
                )
            if not valid:
                raise ValueError(f"Zoo {kind.upper()} output structure is invalid")
            file_name = f"{project_slug}_{kind}_{uuid.uuid4().hex[:8]}{final_suffix}"
            asset = self.asset_service.store_bytes(
                db=db,
                user_id=user_id,
                filename=file_name,
                content_type=(
                    "model/gltf-binary"
                    if kind == "glb"
                    else "application/step"
                ),
                kind="cad",
                source="generated",
                content=output_bytes,
                task_id=task_id,
                metadata={"provider": "zoo", "format": kind},
                publish=publish_assets,
            )
            saved[kind] = str(asset.id)
        return saved

    def persist_generated_image(
        self,
        *,
        db: Session,
        user_id: str,
        image_url: str,
        task_id: str | None = None,
        publish_asset: bool = True,
    ) -> str:
        """将 data URL 图像写入资产表；HTTP URL 由异步下载入口处理。"""
        self._ensure_trusted_image_decoder()
        if not image_url.startswith("data:") or ";base64," not in image_url:
            raise ValueError("image_url must be a base64 data URL")
        header, encoded = image_url.split(",", 1)
        content_type = header[5:].split(";", 1)[0].strip().lower()
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
        }.get(content_type)
        if extension is None:
            raise ValueError(f"unsupported generated image type: {content_type}")
        try:
            content = b64decode(encoded, validate=True)
        except (ValueError, Base64DecodeError) as exc:
            raise ValueError("generated image data URL is invalid") from exc
        if len(content) > self.max_generated_asset_size_bytes:
            raise ValueError("generated image exceeds size limit")
        self._validate_image_content(content_type, content)
        asset = self.asset_service.store_bytes(
            db=db,
            user_id=user_id,
            filename=f"generated_{uuid.uuid4().hex[:16]}.{extension}",
            content_type=content_type,
            kind="image",
            source="generated",
            content=content,
            task_id=task_id,
            metadata={"provider": "image-generation"},
            publish=publish_asset,
        )
        return str(asset.id)

    async def _persist_generated_image_url(
        self,
        *,
        user_id: str,
        image_url: str,
        task_id: str,
        publish_asset: bool,
    ) -> str:
        self._ensure_trusted_image_decoder()
        if image_url.startswith("data:"):
            with self.db_context_factory() as db:
                return self.persist_generated_image(
                    db=db,
                    user_id=user_id,
                    image_url=image_url,
                    task_id=task_id,
                    publish_asset=publish_asset,
                )
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", image_url) as response:
                response.raise_for_status()
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self.max_generated_asset_size_bytes:
                        raise ValueError("generated image exceeds size limit")
                    chunks.append(chunk)
        content = b"".join(chunks)
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
        }.get(content_type)
        if extension is None:
            raise ValueError(
                f"generated image response has unsupported type: {content_type}"
            )
        self._validate_image_content(content_type, content)
        with self.db_context_factory() as db:
            asset = self.asset_service.store_bytes(
                db=db,
                user_id=user_id,
                filename=f"generated_{uuid.uuid4().hex[:16]}.{extension}",
                content_type=content_type,
                kind="image",
                source="generated",
                content=content,
                task_id=task_id,
                metadata={"provider": "image-generation"},
                publish=publish_asset,
            )
            return str(asset.id)

    def _validate_image_content(self, content_type: str, content: bytes) -> None:
        if (
            self.trusted_image_validator is None
            or not self.trusted_image_validator(content_type, content)
        ):
            raise ValueError("generated image media type or content is invalid")

    async def _update_task(
        self,
        workflow_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        outputs: dict[str, JSONValue] | None = None,
        diagnostics: list[dict[str, str]] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        recoverable: bool | None = None,
        require_lease: bool = True,
    ) -> dict[str, JSONValue] | None:
        return await run_in_threadpool(
            self._update_task_sync,
            workflow_id,
            status,
            progress,
            current_step,
            outputs,
            diagnostics,
            error_code,
            error_message,
            recoverable,
            require_lease,
        )

    def _update_task_sync(
        self,
        workflow_id: str,
        status: str | None,
        progress: int | None,
        current_step: str | None,
        outputs: dict[str, JSONValue] | None,
        diagnostics: list[dict[str, str]] | None,
        error_code: str | None,
        error_message: str | None,
        recoverable: bool | None,
        require_lease: bool,
    ) -> dict[str, JSONValue] | None:
        with self.db_context_factory() as db:
            repository = self.repository_factory(db)
            if status in {"completed", "failed"}:
                repository.begin_write_transaction()
                self._finalize_staged_output_assets(
                    db,
                    repository=repository,
                    workflow_id=workflow_id,
                    outputs=outputs or {},
                    publish=status == "completed",
                )
            updated = repository.update_and_append_event(
                workflow_id,
                status=status,
                progress=progress,
                current_step=current_step,
                outputs=outputs,
                diagnostics=cast(list[dict[str, object]] | None, diagnostics),
                error_code=error_code,
                error_message=error_message,
                recoverable=recoverable,
                lease_owner=self.worker_id if require_lease else None,
                event_type="completed" if status == "completed" else (
                    "failed" if status == "failed" else "progress"
                ),
                message=current_step or "",
            )
            return cast(dict[str, JSONValue], updated) if updated is not None else None

    @classmethod
    def _finalize_staged_output_assets(
        cls,
        db: Session,
        *,
        repository: WorkflowTaskRepository,
        workflow_id: str,
        outputs: dict[str, object],
        publish: bool,
    ) -> None:
        referenced_ids = {
            parsed_id
            for key, value in outputs.items()
            if key.endswith("AssetId")
            if (parsed_id := cls._parse_asset_uuid(value)) is not None
        }
        for item in (outputs.get("renderViews") or []):
            if not isinstance(item, dict):
                continue
            nested_id = cls._parse_asset_uuid(item.get("assetId"))
            if nested_id is not None:
                referenced_ids.add(nested_id)
        logger.info(
            "finalize staged assets: workflow=%s publish=%s referenced=%d",
            workflow_id,
            publish,
            len(referenced_ids),
        )
        if not referenced_ids:
            return
        task = repository.get_internal(workflow_id)
        if task is None:
            raise RuntimeError("工作流任务不存在")
        user_id = str(task["userId"])
        assets = list(
            db.scalars(
                select(Asset).where(
                    Asset.id.in_(referenced_ids),
                    Asset.user_id == user_id,
                    Asset.task_id == workflow_id,
                    Asset.status.in_(("staged", "available")),
                )
            )
        )
        if {asset.id for asset in assets} != referenced_ids:
            raise RuntimeError("工作流输出资产不存在、不可用或不属于当前任务")
        for asset in assets:
            if asset.status == "staged":
                asset.status = "available" if publish else "failed"
        db.flush()

    @staticmethod
    def _parse_asset_uuid(value: object) -> UUID | None:
        if not isinstance(value, str):
            return None
        try:
            return UUID(value)
        except ValueError:
            return None

    async def recover_pending_workflows(self) -> None:
        terminal, recoverable = await run_in_threadpool(self._recover_tasks_sync)
        for task in terminal:
            try:
                await self._persist_terminal_history_if_needed(task, None, None)
            except Exception:
                continue
        for task in recoverable:
            task_id = str(task["taskId"])
            try:
                request = IndustrialDesignWorkflowRequest.model_validate(
                    task["inputPayload"]
                )
                project_name = request.project_name or self._default_project_name(request)
                design_spec = cast(dict[str, JSONValue], task.get("designSpec") or {})
            except Exception as exc:
                logger.exception("工作流恢复请求数据无效: %s", task_id)
                failed_task = await self._update_task(
                    task_id,
                    status="failed",
                    progress=100,
                    current_step="工作流恢复失败：持久化请求数据无效",
                    diagnostics=[
                        {
                            "level": "error",
                            "title": "工作流恢复失败",
                            "detail": "持久化请求数据无效，无法恢复任务。",
                        }
                    ],
                    error_code="WORKFLOW_RECOVERY_PAYLOAD_INVALID",
                    error_message="持久化请求数据无效，无法恢复任务。",
                    recoverable=False,
                    require_lease=False,
                )
                if failed_task is not None:
                    await self._persist_terminal_history_if_needed(
                        failed_task,
                        None,
                        None,
                    )
                continue
            self._schedule(
                self._run_external_workflow(
                    task_id,
                    request,
                    project_name,
                    design_spec,
                )
            )

    def _recover_tasks_sync(
        self,
    ) -> tuple[list[dict[str, JSONValue]], list[dict[str, JSONValue]]]:
        with self.db_context_factory() as db:
            repository = self.repository_factory(db)
            recoverable = repository.recover_expired(datetime.now(timezone.utc))
            terminal = repository.list_unpersisted_terminal()
            return (
                cast(list[dict[str, JSONValue]], terminal),
                cast(list[dict[str, JSONValue]], recoverable),
            )

    @staticmethod
    def _resolve_user_id(auth_user: dict[str, object] | None) -> str:
        if auth_user is None:
            return auth_user_id({})
        return auth_user_id(auth_user)

    @staticmethod
    def _slugify(value: str) -> str:
        text = "".join(ch if ch.isalnum() else "_" for ch in value.strip())
        text = "_".join(part for part in text.split("_") if part)
        return text[:48] or "industrial_design"

    @staticmethod
    def _asset_url(asset_id: str) -> str:
        return f"{settings.API_V1_PREFIX}/assets/{asset_id}/download"

    @staticmethod
    def _default_project_name(request: IndustrialDesignWorkflowRequest) -> str:
        if request.asset_metas:
            return f"{request.asset_metas[0].filename}二创设计"
        return "工业品智能设计项目"


industrial_design_workflow_service = IndustrialDesignWorkflowService()
