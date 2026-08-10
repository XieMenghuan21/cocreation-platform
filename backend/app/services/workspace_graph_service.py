"""Workspace Graph 服务：Node 的创建/更新/查询，统一维护业务状态与投影。

原则：WorkspaceNode 是聊天卡片、项目树、Preview 的唯一业务数据源。
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.conversation import Conversation
from app.models.orchestration import AgentRun, WorkflowInstance
from app.models.workspace_node import WorkspaceNode, WorkspaceNodeAsset
from app.schemas.workspace_graph import WorkspaceNodeData, WorkspaceNodeAssetData
from app.services.orchestration.contracts import AgentExecutionResult
from app.services.orchestration.runtime import OrchestrationRuntime


AGENT_KEY_TO_TYPE: dict[str, str] = {
    "requirement_agent": "requirement",
    "project_agent": "project",
    "design_agent": "design",
    "render_agent": "render",
    "model_agent": "three_d",
    "cad_agent": "cad",
    "quote_agent": "quote",
    "engineering_agent": "engineering_package",
    "engineering_package_agent": "engineering_package",
}


class WorkspaceGraphService:
    @staticmethod
    def create_node(
        db: Session,
        *,
        user_id: str,
        conversation_id: UUID,
        node_type: str,
        title: str,
        summary: str = "",
        project_id: str | None = None,
        parent_id: UUID | None = None,
        branch_id: UUID | None = None,
        status: str = "draft",
        agent_key: str | None = None,
        task_id: str | None = None,
        version_id: str | None = None,
        input_data: dict[str, object] | None = None,
        output_data: dict[str, object] | None = None,
        ui_data: dict[str, object] | None = None,
    ) -> WorkspaceNode:
        node = WorkspaceNode(
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=project_id,
            parent_id=parent_id,
            branch_id=branch_id,
            node_type=node_type,
            status=status,
            title=title,
            summary=summary,
            agent_key=agent_key,
            task_id=task_id,
            version_id=version_id,
            input_data=input_data or {},
            output_data=output_data or {},
            ui_data=ui_data or {},
        )
        db.add(node)
        db.flush()
        WorkspaceGraphService._ensure_agent_run(db, node)
        return node

    @staticmethod
    def update_node(
        db: Session,
        *,
        node_id: UUID,
        user_id: str,
        **fields: object,
    ) -> WorkspaceNode | None:
        node = db.scalar(
            select(WorkspaceNode).where(
                WorkspaceNode.id == node_id,
                WorkspaceNode.user_id == user_id,
            )
        )
        if node is None:
            return None
        for key, value in fields.items():
            if key in {
                "node_type",
                "status",
                "title",
                "summary",
                "agent_key",
                "task_id",
                "version_id",
                "project_id",
                "parent_id",
                "branch_id",
            }:
                setattr(node, key, value)
            elif key in {"input_data", "output_data", "ui_data"} and isinstance(value, dict):
                setattr(node, key, value)
        db.flush()
        if "status" in fields:
            WorkspaceGraphService._sync_agent_run_status(db, node)
        return node

    @staticmethod
    def link_asset(
        db: Session,
        *,
        node_id: UUID,
        asset_id: UUID,
        role: str = "reference",
    ) -> WorkspaceNodeAsset | None:
        existing = db.scalar(
            select(WorkspaceNodeAsset).where(
                WorkspaceNodeAsset.node_id == node_id,
                WorkspaceNodeAsset.asset_id == asset_id,
                WorkspaceNodeAsset.role == role,
            )
        )
        if existing is not None:
            return existing
        link = WorkspaceNodeAsset(
            node_id=node_id,
            asset_id=asset_id,
            role=role,
        )
        db.add(link)
        db.flush()
        return link

    @staticmethod
    def get_nodes(
        db: Session,
        *,
        user_id: str,
        conversation_id: UUID,
    ) -> list[WorkspaceNode]:
        return list(
            db.scalars(
                select(WorkspaceNode)
                .options(selectinload(WorkspaceNode.assets))
                .where(
                    WorkspaceNode.user_id == user_id,
                    WorkspaceNode.conversation_id == conversation_id,
                )
                .order_by(WorkspaceNode.created_at.asc())
            )
        )

    @staticmethod
    def get_node(
        db: Session,
        *,
        user_id: str,
        node_id: UUID,
    ) -> WorkspaceNode | None:
        return db.scalar(
            select(WorkspaceNode)
            .options(selectinload(WorkspaceNode.assets))
            .where(
                WorkspaceNode.id == node_id,
                WorkspaceNode.user_id == user_id,
            )
        )

    @staticmethod
    def supersede_siblings(
        db: Session,
        *,
        user_id: str,
        node_id: UUID,
        node_type: str,
        conversation_id: UUID,
    ) -> list[WorkspaceNode]:
        """把同类型同会话下其他节点标记为 superseded（用于设计方向 A/B/C 选择）。"""
        updated: list[WorkspaceNode] = []
        candidates = db.scalars(
            select(WorkspaceNode).where(
                WorkspaceNode.user_id == user_id,
                WorkspaceNode.conversation_id == conversation_id,
                WorkspaceNode.node_type == node_type,
                WorkspaceNode.id != node_id,
                WorkspaceNode.status.in_(["waiting_user", "draft", "completed"]),
            )
        )
        for node in candidates:
            node.status = "superseded"
            updated.append(node)
        db.flush()
        return updated

    @staticmethod
    def to_data(node: WorkspaceNode) -> WorkspaceNodeData:
        return WorkspaceNodeData(
            id=node.id,
            conversation_id=node.conversation_id,
            project_id=node.project_id,
            parent_id=node.parent_id,
            branch_id=node.branch_id,
            node_type=node.node_type,
            status=node.status,
            title=node.title,
            summary=node.summary,
            agent_key=node.agent_key,
            task_id=node.task_id,
            version_id=node.version_id,
            input_data=node.input_data,
            output_data=node.output_data,
            ui_data=node.ui_data,
            assets=[
                WorkspaceNodeAssetData(
                    id=link.id,
                    asset_id=link.asset_id,
                    role=link.role,
                    created_at=link.created_at,
                )
                for link in node.assets
            ],
            created_at=node.created_at,
            updated_at=node.updated_at,
        )

    @staticmethod
    def _ensure_agent_run(db: Session, node: WorkspaceNode) -> None:
        """Create the execution record behind an agent-backed WorkspaceNode."""
        if not node.agent_key:
            return
        if isinstance(node.ui_data, dict) and node.ui_data.get("agentRunId"):
            return
        agent_type = AGENT_KEY_TO_TYPE.get(node.agent_key)
        if agent_type is None:
            return
        project_id = WorkspaceGraphService._resolve_project_id(db, node)
        if not project_id:
            return

        workflow = WorkspaceGraphService._get_or_create_workflow(
            db,
            user_id=node.user_id,
            project_id=project_id,
            conversation_id=node.conversation_id,
        )
        runtime = OrchestrationRuntime(db)
        run = runtime.enqueue_agent(
            workflow_id=str(workflow.id),
            agent_type=agent_type,
            input_snapshot={
                "workspaceNodeId": str(node.id),
                "nodeType": node.node_type,
                "title": node.title,
                "summary": node.summary,
                "inputData": node.input_data,
            },
        )
        ui_data = dict(node.ui_data or {})
        ui_data["workflowId"] = str(workflow.id)
        ui_data["agentRunId"] = str(run.id)
        node.ui_data = ui_data
        WorkspaceGraphService._apply_node_status_to_agent_run(db, node, run)
        db.flush()

    @staticmethod
    def _sync_agent_run_status(db: Session, node: WorkspaceNode) -> None:
        if not isinstance(node.ui_data, dict):
            return
        run_id = node.ui_data.get("agentRunId")
        if not isinstance(run_id, str) or not run_id:
            return
        try:
            run_uuid = UUID(run_id)
        except ValueError:
            return
        run = db.get(AgentRun, run_uuid)
        if run is None or run.user_id != node.user_id:
            return
        WorkspaceGraphService._apply_node_status_to_agent_run(db, node, run)
        db.flush()

    @staticmethod
    def _apply_node_status_to_agent_run(
        db: Session,
        node: WorkspaceNode,
        run: AgentRun,
    ) -> None:
        runtime = OrchestrationRuntime(db)
        if node.status in {"queued", "draft"}:
            run.status = "queued"
            return
        if node.status == "running":
            runtime.mark_running(run)
            return
        if node.status == "waiting_user":
            runtime.mark_waiting_user(
                run,
                AgentExecutionResult(
                    status="waiting_user",
                    output_snapshot={
                        "workspaceNodeId": str(node.id),
                        "outputData": node.output_data,
                        "uiData": node.ui_data,
                    },
                    artifact_ids=(),
                    next_agents=(),
                    message=f"{node.title} waiting for user",
                ),
            )
            return
        if node.status == "completed":
            runtime.mark_succeeded(
                run,
                AgentExecutionResult(
                    status="succeeded",
                    output_snapshot={
                        "workspaceNodeId": str(node.id),
                        "outputData": node.output_data,
                        "uiData": node.ui_data,
                    },
                    artifact_ids=(),
                    next_agents=(),
                    message=f"{node.title} completed",
                ),
            )
            return
        if node.status == "failed":
            output = node.output_data if isinstance(node.output_data, dict) else {}
            message = str(output.get("errorMessage") or f"{node.title} failed")
            runtime.mark_failed(
                run,
                error_code=str(output.get("errorCode") or "AGENT_FAILED"),
                error_message=message,
            )
            return
        if node.status == "superseded":
            run.status = "skipped"

    @staticmethod
    def _resolve_project_id(db: Session, node: WorkspaceNode) -> str | None:
        if node.project_id:
            return node.project_id
        conversation = db.get(Conversation, node.conversation_id)
        if conversation is not None and conversation.project_id:
            return conversation.project_id
        return None

    @staticmethod
    def _get_or_create_workflow(
        db: Session,
        *,
        user_id: str,
        project_id: str,
        conversation_id: UUID,
    ) -> WorkflowInstance:
        workflow = db.scalar(
            select(WorkflowInstance)
            .where(
                WorkflowInstance.user_id == user_id,
                WorkflowInstance.project_id == project_id,
                WorkflowInstance.conversation_id == conversation_id,
            )
            .order_by(WorkflowInstance.created_at.asc())
        )
        if workflow is not None:
            return workflow
        return OrchestrationRuntime(db).create_workflow(
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            initial_input={"source": "workspace_graph"},
        )


workspace_graph_service = WorkspaceGraphService()
