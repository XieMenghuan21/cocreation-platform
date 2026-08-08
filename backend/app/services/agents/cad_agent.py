"""CadAgent：把「生成 CAD」建模为 cad 节点并复用现有工件工作流。

重用 industrial_design_workflow_service 的 generate_cad / generateDrawing 能力，
前端通过节点快照感知进度。复用 render_agent 的 sync_node_from_task 做任务→节点同步。
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


class CadAgentError(Exception):
    pass


def build_cad_request(
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
            generateThreePreview=False,
            generateExplosion=False,
            generatePlanLine=False,
            generateDrawing=True,
            generateRender=False,
        ),
    )


class CadAgent:
    async def launch_cad(
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
        request = build_cad_request(
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
                ui_data={**node.ui_data, "taskId": task_id, "currentStep": "生成 CAD 图纸"},
            ) or node
        db.flush()
        return node


cad_agent = CadAgent()