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
        if request.options.generate_cad or request.options.generate_three_preview or request.options.generate_plan_line:
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
            and request.options.generate_render
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

        if request.options.generate_three_preview:
            outputs["threePreview"] = self._build_three_preview_spec(project_name, request)
        if request.options.generate_render:
            outputs.setdefault("renderPng", None)
        if request.options.generate_explosion:
            outputs.setdefault("explosionPng", None)
        if request.options.enhance_image and request.options.generate_render:
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
                self._apply_image_edit_result(outputs, image_edit_result)

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
        elif request.options.generate_cad or request.options.generate_three_preview:
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
                    self._apply_image_edit_result(outputs, image_edit_result)
                    await self._update_task(
                        workflow_id,
                        progress=80,
                        current_step="已基于参考设计图生成场景融合宣发图。",
                        outputs=outputs,
                        diagnostics=diagnostics,
                    )
            else:
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

        if request.options.generate_cad or request.options.generate_three_preview:
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

        if request.options.generate_three_preview:
            outputs.setdefault("threePreview", self._build_three_preview_spec(project_name, request))
        if request.options.generate_explosion:
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
        if request.options.enhance_image and request.options.generate_render and not outputs.get("enhancedImage"):
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
                self._apply_image_edit_result(outputs, image_edit_result)

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
                    if item.get("level") == "warning" and item.get("detail")
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
                    f"Commercial-grade home furniture visualization for project '{project_name}'.",
                    f"Industry: {design_spec['industry']}.",
                    "Style: premium furniture product render, soft home-lifestyle atmosphere, realistic material texture, refined surface detail.",
                    "Quality: high resolution, accurate color, natural shadow, stable composition, no decorative noise.",
                    "Lighting: studio softbox lighting with subtle home ambience, true-to-life material appearance.",
                    "Output: marketing-quality furniture visual suitable for design review and client presentation.",
                ]
            else:
                lines = [
                    f"Commercial-grade industrial product visualization for project '{project_name}'.",
                    f"Industry: {design_spec['industry']}.",
                    "Style: professional product rendering, realistic material texture, refined surface detail, presentation-ready.",
                    "Quality: high resolution, accurate color, natural shadow, stable composition, no decorative noise.",
                    "Lighting: studio softbox lighting, subtle environment reflection, true-to-life material appearance.",
                    "Output: marketing-quality visual suitable for design review and client presentation.",
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
        # 默认固定走自建 ComfyUI（FLUX.1-schnell），不自动切换其他供应商。
        return await self.ai_model_gateway.generate_design_image(
            prompt=prompt,
            images=images,
            optimize_prompt=optimize_prompt,
            provider="comfyui",
        )

    def _image_provider_label(self) -> str:
        return "本地 ComfyUI"

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
                "detail": "宣发图必须基于参考设计图编辑生成，本次图片编辑失败。",
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
    def _apply_image_edit_result(
        outputs: dict[str, JSONValue],
        image_edit_result: dict[str, str],
    ) -> None:
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
        lines = [
            f"基于参考图做图片编辑和场景融合，项目名称：{project_name}",
            f"所属行业：{design_spec['industry']}",
            "必须保留参考图中的主体产品、外形轮廓、结构比例、关键部件和材质特征，不要重新设计一个新产品。",
            "只允许把参考图主体融合到真实使用场景中，补充空间背景、光影、材质细节和商业摄影质感。",
            "如果参考图是设计版面或多视图图纸，请提取其中的主产品形态作为同一产品，不要改变成其他款式。",
            "输出为产品场景融合宣发图，主体应与参考图明显一致，适合客户展示和营销使用。",
            "不要添加无关文字、尺寸标注、夸张广告元素、错误结构或过度艺术化背景。",
            request.text or "",
        ]
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
