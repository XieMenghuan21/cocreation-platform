"""Workspace Graph 服务：Node 的创建/更新/查询，统一维护业务状态与投影。

原则：WorkspaceNode 是聊天卡片、项目树、Preview 的唯一业务数据源。
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.workspace_node import WorkspaceNode, WorkspaceNodeAsset
from app.schemas.workspace_graph import WorkspaceNodeData, WorkspaceNodeAssetData


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


workspace_graph_service = WorkspaceGraphService()
