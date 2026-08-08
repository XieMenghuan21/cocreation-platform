"""RenderAgent：挂接现有工业品设计工作流，渲染节点原地更新。"""
from __future__ import annotations

import json
import logging
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


def build_render_request(
    *,
    project_name: str,
    requirement_text: str,
    direction_image_prompt: str | None,
    industry: str | None,
) -> object:
    """构造工业品设计工作流请求。"""
    from app.schemas.industrial_design import (
        IndustrialDesignWorkflowOptions,
        IndustrialDesignWorkflowRequest,
    )

    text = direction_image_prompt or requirement_text
    return IndustrialDesignWorkflowRequest(
        projectName=project_name,
        industry=industry or "装备制造",
        inputType="text",
        text=text,
        mode="create",
        options=IndustrialDesignWorkflowOptions(
            generateCad=False,
            generateThreePreview=False,
            generateExplosion=False,
            generatePlanLine=False,
            generateDrawing=True,
            generateRender=True,
        ),
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
    if image_url and not new_output.get("previewUrl"):
        new_output["previewUrl"] = image_url
        new_output["renderImageUrl"] = image_url
        new_output["imageUrl"] = image_url
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
            db,
            user_id=node.user_id,
            conversation_id=node.conversation_id,
            project_id=node.project_id,
            parent_id=node.id,
            source_node_type=node.node_type,
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
    ) -> WorkspaceNode:
        """创建/更新 render 节点并启动后台工作流。"""
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

        request = build_render_request(
            project_name=project_name,
            requirement_text=requirement_text,
            direction_image_prompt=direction_image_prompt,
            industry=industry,
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
            node = workspace_graph_service.update_node(
                db,
                node_id=node.id,
                user_id=user_id,
                task_id=task_id,
                status="running",
                ui_data={
                    **node.ui_data,
                    "taskId": task_id,
                    "currentStep": result.get("currentStep") or "",
                },
            ) or node
        db.flush()
        return node


render_agent = RenderAgent()
