"""EngineeringAgent：把现有 engineering_package_service 变成 Workspace 节点。

不重写工程包服务。Agent 负责读取最新可用模型/CAD 任务 → build_package →
创建 engineering_package 节点并挂上 asset；若缺少模型则返回 next_action 提示。
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.persistence import WorkflowTask
from app.models.workspace_node import WorkspaceNode
from app.services.engineering_package_service import (
    EngineeringPackageServiceError,
    engineering_package_service,
)
from app.services.workspace_graph_service import workspace_graph_service

logger = logging.getLogger(__name__)


class EngineeringAgentError(Exception):
    pass


class EngineeringAgent:
    def _latest_model_task(
        self,
        db: Session,
        *,
        user_id: str,
        project_id: str | None,
    ) -> WorkflowTask | None:
        """找最新一个已完成且含 3D 模型的任务。"""
        query = select(WorkflowTask).where(
            WorkflowTask.user_id == user_id,
            WorkflowTask.status == "completed",
        )
        if project_id:
            query = query.where(WorkflowTask.project_id == project_id)
        query = query.order_by(WorkflowTask.updated_at.desc())
        for task in db.scalars(query):
            outputs = task.outputs or {}
            if outputs.get("modelStlAssetId") or outputs.get("modelStepAssetId"):
                return task
        return None

    def build_package_node(
        self,
        *,
        db: Session,
        auth_user: dict[str, object],
        user_id: str,
        conversation_id: UUID,
        project_id: str | None,
        project_name: str,
        source_node: WorkspaceNode,
    ) -> tuple[WorkspaceNode | None, str]:
        """生成工程包节点。返回 (node, assistant_text)；node 为 None 表示缺少前置产物。"""
        task = self._latest_model_task(db, user_id=user_id, project_id=project_id)
        if task is None:
            return None, (
                "当前还缺少可用于工程包的 CAD/模型结果，我建议先生成三维模型或 CAD。"
            )

        node = workspace_graph_service.create_node(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=project_id,
            parent_id=source_node.id,
            node_type="engineering_package",
            status="running",
            title=f"工程包「{project_name}」",
            summary=f"基于 {task.project_id or project_name} 组装 ZIP/PDF/BOM。",
            agent_key="engineering_agent",
            task_id=task.id,
            output_data={},
        )
        db.flush()

        try:
            result = engineering_package_service.build_package(
                db=db,
                user_id=user_id,
                task_id=task.id,
                publish_assets=True,
            )
        except EngineeringPackageServiceError as exc:
            node = workspace_graph_service.update_node(
                db,
                node_id=node.id,
                user_id=user_id,
                status="failed",
                output_data={"errorMessage": exc.message},
            ) or node
            raise EngineeringAgentError(exc.message) from exc

        package_asset_id = result.get("packageAssetId")
        if package_asset_id:
            from uuid import UUID as _UUID

            try:
                workspace_graph_service.link_asset(
                    db,
                    node_id=node.id,
                    asset_id=_UUID(str(package_asset_id)),
                    role="package",
                )
            except Exception:
                logger.exception("工程包 asset 关联失败")

        node = workspace_graph_service.update_node(
            db,
            node_id=node.id,
            user_id=user_id,
            status="completed",
            output_data={
                "packageAssetId": package_asset_id,
                "downloadUrl": result.get("packageDownloadUrl"),
                "filename": result.get("filename") or "工程包.zip",
            },
        ) or node
        return node, f"工程包已生成：{result.get('filename') or '工程包.zip'}"


engineering_agent = EngineeringAgent()