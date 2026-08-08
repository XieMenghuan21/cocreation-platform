"""Legacy → WorkspaceNode 镜像服务。

设计目标：
1. 幂等：同一个 sourceKey 重复写入只更新同一个 Node。
2. 非侵入：不调用 Agent、不创建 WorkflowTask、不改变旧业务状态机。
3. 可回滚：删除调用方也不会影响原 Conversation / Project / Asset / WorkflowTask。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.workspace_mirror import WorkspaceMirrorRequest
from app.services.workspace_graph_service import workspace_graph_service


class WorkspaceMirrorService:
    @staticmethod
    def _source_key(node: object) -> str | None:
        ui_data = getattr(node, "ui_data", None)
        if not isinstance(ui_data, dict):
            return None
        value = ui_data.get("legacySourceKey")
        return str(value) if value else None

    def mirror(
        self,
        db: Session,
        *,
        user_id: str,
        conversation_id,
        request: WorkspaceMirrorRequest,
    ):
        nodes = workspace_graph_service.get_nodes(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        existing = next(
            (node for node in nodes if self._source_key(node) == request.source_key),
            None,
        )
        parent = None
        if request.parent_source_key:
            parent = next(
                (node for node in nodes if self._source_key(node) == request.parent_source_key),
                None,
            )

        ui_data = dict(request.ui_data)
        ui_data["legacySourceKey"] = request.source_key
        ui_data["mirrorMode"] = True

        if existing is not None:
            return workspace_graph_service.update_node(
                db,
                node_id=existing.id,
                user_id=user_id,
                node_type=request.node_type,
                status=request.status,
                title=request.title,
                summary=request.summary,
                project_id=request.project_id,
                task_id=request.task_id,
                version_id=request.version_id,
                parent_id=parent.id if parent else existing.parent_id,
                input_data=request.input_data,
                output_data=request.output_data,
                ui_data=ui_data,
            )

        return workspace_graph_service.create_node(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            node_type=request.node_type,
            status=request.status,
            title=request.title,
            summary=request.summary,
            project_id=request.project_id,
            task_id=request.task_id,
            version_id=request.version_id,
            parent_id=parent.id if parent else None,
            input_data=request.input_data,
            output_data=request.output_data,
            ui_data=ui_data,
        )


workspace_mirror_service = WorkspaceMirrorService()
