"""ModelAgent：把「生成 3D 模型」建模为 model_3d 节点并复用工件工作流。

不重写现有模型生成服务；只是把工业品工作流的 generate_cad / generateThreePreview
编排到一个 model_3d Workspace 节点上，前端通过节点快照感知进度。
"""
from __future__ import annotations

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


class ModelAgentError(Exception):
    pass


def build_3d_request(
    *,
    project_name: str,
    requirement_text: str,
    industry: str | None,
) -> object:
    from app.schemas.industrial_design import (
        IndustrialDesignWorkflowOptions,
        IndustrialDesignWorkflowRequest,
    )

    return IndustrialDesignWorkflowRequest(
        projectName=project_name,
        industry=industry or "装备制造",
        inputType="text",
        text=requirement_text,
        mode="create",
        options=IndustrialDesignWorkflowOptions(
            generateCad=True,
            generateThreePreview=True,
            generateExplosion=False,
            generatePlanLine=False,
            generateDrawing=False,
            generateRender=False,
        ),
    )


class ModelAgent:
    async def launch_3d(
        self,
        *,
        db: Session,
        auth_user: dict[str, object],
        user_id: str,
        node: WorkspaceNode,
        project_name: str,
        requirement_text: str,
        industry: str | None,
    ) -> WorkspaceNode:
        request = build_3d_request(
            project_name=project_name,
            requirement_text=requirement_text,
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
                ui_data={**node.ui_data, "taskId": task_id, "currentStep": "构建 3D 模型"},
            ) or node
        db.flush()
        return node


model_agent = ModelAgent()