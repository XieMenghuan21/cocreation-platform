"""RenderAgent：挂接现有工业品设计工作流，渲染节点原地更新。"""
from __future__ import annotations

import json
import logging
import re
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.persistence import WorkflowTask
from app.models.workspace_node import WorkspaceNode
from app.services.industrial_design_workflow_service import (
    industrial_design_workflow_service,
)
from app.services.workspace_graph_service import workspace_graph_service

logger = logging.getLogger(__name__)


class RenderAgentError(Exception):
    pass


RenderMode = Literal["design", "promotion"]


def _extract_asset_ids_from_urls(urls: list[str]) -> list[str]:
    ids: list[str] = []
    for url in urls:
        match = re.search(r"/assets/([0-9a-fA-F-]{36})/download", url)
        if not match:
            continue
        asset_id = match.group(1)
        if asset_id not in ids:
            ids.append(asset_id)
    return ids


def build_render_request(
    *,
    project_name: str,
    requirement_text: str,
    direction_image_prompt: str | None,
    industry: str | None,
    reference_image_urls: list[str],
    render_mode: RenderMode = "design",
) -> object:
    """构造工业品设计工作流请求。

    render_mode="design"：文生图设计稿，不需要参考图，不启用图片编辑。
    render_mode="promotion"：图生图宣发图，必须提供参考图，启用图片编辑。
    """
    from app.schemas.industrial_design import (
        IndustrialDesignWorkflowOptions,
        IndustrialDesignWorkflowRequest,
    )

    text = direction_image_prompt or requirement_text
    context: dict[str, object] = {}
    if render_mode == "promotion":
        context["imageEditMode"] = "poster"

    return IndustrialDesignWorkflowRequest(
        projectName=project_name,
        industry=industry or "装备制造",
        inputType="text",
        text=text,
        assetIds=_extract_asset_ids_from_urls(reference_image_urls),
        assetUrls=reference_image_urls,
        mode="create",
        options=IndustrialDesignWorkflowOptions(
            generateCad=False,
            generateThreePreview=False,
            generateExplosion=False,
            generatePlanLine=False,
            generateDrawing=False,
            generateRender=True,
            enhanceImage=(render_mode == "promotion"),
        ),
        context=context,
    )


def _pick_output_image(outputs: dict[str, object]) -> str | None:
    for key in ("renderImageUrl", "imageUrl", "drawingUrl", "previewUrl"):
        value = outputs.get(key)
        if isinstance(value, str) and value:
            return value
    generated = outputs.get("generatedImages")
    if isinstance(generated, list):
        for item in generated:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                return item["url"]
    return None


def _first_string(data: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _collect_reference_image_urls(db: Session, node: WorkspaceNode) -> list[str]:
    """Resolve the upstream image that render must edit instead of text-to-image."""
    urls: list[str] = []
    image_keys = (
        "referenceImageUrl",
        "sourceImageUrl",
        "renderImageUrl",
        "renderPng",
        "enhancedImage",
        "imageUrl",
        "previewUrl",
        "drawingUrl",
    )

    current: WorkspaceNode | None = node
    visited: set[UUID] = set()
    while current is not None and current.id not in visited:
        visited.add(current.id)
        for bucket in (current.output_data, current.input_data, current.ui_data):
            if not isinstance(bucket, dict):
                continue
            direct = _first_string(bucket, image_keys)
            if direct and direct not in urls:
                urls.append(direct)
            workflow_outputs = bucket.get("workflowOutputs")
            if isinstance(workflow_outputs, dict):
                nested = _first_string(workflow_outputs, image_keys)
                if nested and nested not in urls:
                    urls.append(nested)
        if current.parent_id is None:
            break
        current = db.get(WorkspaceNode, current.parent_id)
    return urls


def _resolve_source_image_metadata(
    db: Session,
    node: WorkspaceNode,
) -> tuple[str | None, str | None]:
    """Return the first upstream image URL and its node title for UI traceability."""
    image_keys = (
        "referenceImageUrl",
        "sourceImageUrl",
        "renderImageUrl",
        "renderPng",
        "enhancedImage",
        "imageUrl",
        "previewUrl",
        "drawingUrl",
    )

    current: WorkspaceNode | None = node
    visited: set[UUID] = set()
    while current is not None and current.id not in visited:
        visited.add(current.id)
        for bucket in (current.output_data, current.input_data, current.ui_data):
            if not isinstance(bucket, dict):
                continue
            direct = _first_string(bucket, image_keys)
            if direct:
                return direct, current.title
            workflow_outputs = bucket.get("workflowOutputs")
            if isinstance(workflow_outputs, dict):
                nested = _first_string(workflow_outputs, image_keys)
                if nested:
                    return nested, current.title
        if current.parent_id is None:
            break
        current = db.get(WorkspaceNode, current.parent_id)
    return None, None


def sync_node_from_task(db: Session, task_id: str) -> WorkspaceNode | None:
    """把 WorkflowTask 的最新状态同步到关联的 WorkspaceNode（原地更新）。"""
    task = db.scalar(select(WorkflowTask).where(WorkflowTask.id == task_id))
    if task is None or not task.workspace_node_id:
        return None
    node = workspace_graph_service.get_node(
        db, user_id=task.user_id, node_id=task.workspace_node_id
    )
    if node is None:
        return None

    status_map = {
        "pending": "queued",
        "queued": "queued",
        "running": "running",
        "completed": "completed",
        "completed_with_errors": "completed",
        "failed": "failed",
    }
    new_status = status_map.get(task.status, node.status)
    outputs = task.outputs or {}

    updates: dict[str, object] = {}
    if new_status != node.status:
        updates["status"] = new_status
    if task.progress is not None and task.progress != node.ui_data.get("progress"):
        updates["ui_data"] = {**node.ui_data, "progress": task.progress}

    image_url = _pick_output_image(outputs)
    new_output: dict[str, object] = dict(node.output_data)

    # renderPng/enhancedImage 是 Graph 渲染主产物，也提升为 previewUrl 供资源页/预览通用。
    if image_url is None:
        for key in ("renderPng", "enhancedImage"):
            value = outputs.get(key)
            if isinstance(value, str) and value:
                image_url = value
                break

    # WorkspaceNode 是 Chat / ResourceTree / Preview 的投影源。
    # 不能只复制一张预览图，否则 3D/CAD/工程包在 Graph 模式下会“任务完成但没有可预览产物”。
    # 保存完整工作流 outputs，同时把常用字段提升到顶层以兼容现有前端。
    if outputs:
        new_output["workflowOutputs"] = outputs
        passthrough_keys = (
            "renderPng", "enhancedImage", "explosionPng", "drawingSvg",
            "modelStl", "modelStep", "modelGlb", "modelDownloadUrl",
            "planLineSvg", "generatedImageUrls", "generatedImages",
            "modelStlAssetId", "modelStepAssetId", "drawingAssetId",
        )
        for key in passthrough_keys:
            value = outputs.get(key)
            if value not in (None, "", [], {}):
                new_output[key] = value

    if image_url:
        new_output["previewUrl"] = image_url
        new_output["renderImageUrl"] = image_url
        new_output["imageUrl"] = image_url

    model_url = outputs.get("modelStl") or outputs.get("modelDownloadUrl") or outputs.get("modelGlb")
    if isinstance(model_url, str) and model_url:
        new_output["modelUrl"] = model_url

    drawing_url = outputs.get("drawingSvg") or outputs.get("planLineSvg")
    if isinstance(drawing_url, str) and drawing_url:
        new_output["drawingUrl"] = drawing_url

    if task.error_message and new_status == "failed":
        new_output["errorMessage"] = task.error_message
    updates["output_data"] = new_output

    updated = workspace_graph_service.update_node(
        db, node_id=node.id, user_id=node.user_id, **updates
    )

    # 节点完成时生成「下一步」推荐节点（§41/42：不写死流程终点）。
    if new_status == "completed" and node.node_type in {"render", "model_3d", "cad"}:
        from app.services.agents.next_action_agent import next_action_agent

        next_action_agent.create_next_action(
            db=db,
            user_id=node.user_id,
            conversation_id=node.conversation_id,
            project_id=node.project_id,
            parent_id=node.id,
            source_node_type=node.node_type,
            source_node=updated or node,
        )

    return updated or node


class RenderAgent:
    async def launch_render(
        self,
        *,
        db: Session,
        auth_user: dict[str, object],
        user_id: str,
        node: WorkspaceNode,
        project_name: str,
        requirement_text: str,
        direction_image_prompt: str | None,
        industry: str | None,
        render_mode: RenderMode | None = None,
    ) -> WorkspaceNode:
        """创建/更新 render 节点并启动后台工作流。

        render_mode 优先级：显式参数 > node.input_data > node.output_data > "design"。
        - "design"：文生图设计稿，不需要参考图。
        - "promotion"：图生图宣发图，必须基于已有设计图/参考图。
        """
        effective_mode = (
            render_mode
            or (node.input_data.get("renderMode") if isinstance(node.input_data, dict) else None)
            or (node.output_data.get("renderMode") if isinstance(node.output_data, dict) else None)
            or "design"
        )

        if node.node_type != "render":
            node = workspace_graph_service.create_node(
                db,
                user_id=user_id,
                conversation_id=node.conversation_id,
                project_id=node.project_id,
                parent_id=node.id,
                node_type="render",
                status="queued",
                title=f"渲染方案「{project_name}」",
                summary=direction_image_prompt or requirement_text,
                agent_key="render_agent",
                input_data={
                    "requirement": requirement_text,
                    "imagePrompt": direction_image_prompt or "",
                },
            )

        reference_image_urls = _collect_reference_image_urls(db, node)

        # promotion 模式必须有参考图，否则直接失败
        if effective_mode == "promotion" and not reference_image_urls:
            node = workspace_graph_service.update_node(
                db,
                node_id=node.id,
                user_id=user_id,
                status="failed",
                output_data={
                    **dict(node.output_data or {}),
                    "errorCode": "RENDER_REFERENCE_IMAGE_REQUIRED",
                    "errorMessage": "宣发图已固定为图片编辑，必须先有一张设计图或参考图。",
                },
                ui_data={
                    **dict(node.ui_data or {}),
                    "currentStep": "缺少参考图，未启动文生图。",
                    "progress": 100,
                },
            ) or node
            db.flush()
            return node

        # design 模式没有参考图也允许纯文生图
        source_image_url: str | None = None
        source_node_title: str | None = None
        if reference_image_urls:
            source_image_url, source_node_title = _resolve_source_image_metadata(db, node)
            source_image_url = source_image_url or reference_image_urls[0]
            source_node_title = source_node_title or "设计图"

        request = build_render_request(
            project_name=project_name,
            requirement_text=requirement_text,
            direction_image_prompt=direction_image_prompt,
            industry=industry,
            reference_image_urls=reference_image_urls,
            render_mode=effective_mode,
        )
        result = await industrial_design_workflow_service.create_workflow(
            request,
            auth_user=auth_user,
        )
        task_id = str(result.get("taskId") or "")
        if task_id:
            wf_task = db.scalar(select(WorkflowTask).where(WorkflowTask.id == task_id))
            if wf_task is not None:
                wf_task.workspace_node_id = node.id
                wf_task.conversation_id = node.conversation_id

            new_output: dict[str, object] = {
                **dict(node.output_data or {}),
                "renderMode": effective_mode,
            }
            new_ui: dict[str, object] = {
                **dict(node.ui_data or {}),
                "taskId": task_id,
                "currentStep": result.get("currentStep") or "",
                "renderMode": effective_mode,
            }
            if effective_mode == "promotion" and source_image_url:
                new_output["sourceImageUrl"] = source_image_url
                new_output["sourceNodeTitle"] = source_node_title
                new_ui["sourceImageUrl"] = source_image_url
                new_ui["sourceNodeTitle"] = source_node_title

            node = workspace_graph_service.update_node(
                db,
                node_id=node.id,
                user_id=user_id,
                task_id=task_id,
                status="running",
                output_data=new_output,
                ui_data=new_ui,
            ) or node
        db.flush()
        return node


render_agent = RenderAgent()
