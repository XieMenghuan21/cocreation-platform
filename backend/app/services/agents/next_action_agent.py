"""NextActionAgent：在节点完成后生成推荐「下一步」节点。

禁止写死「流程终点」；每个 Agent 完成时决定是否创建 next_action 节点。
第一阶段只作为推荐渲染数据，用户确认后由 Orchestrator 启动对应 Agent。
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.workspace_node import WorkspaceNode
from app.services.workspace_graph_service import workspace_graph_service

# node_type -> 推荐下一步
_NEXT_STEPS: dict[str, list[dict[str, object]]] = {
    "render": [
        {
            "type": "generate_3d",
            "label": "建立 3D 模型",
            "description": "检查比例、结构与装配关系",
        },
        {
            "type": "generate_cad",
            "label": "生成 CAD 图纸",
            "description": "输出工程图与可加工数据",
        },
        {
            "type": "generate_quote",
            "label": "生成报价",
            "description": "基于当前方案估算设计服务费用",
        },
    ],
    "model_3d": [
        {
            "type": "generate_cad",
            "label": "生成 CAD",
            "description": "把 3D 转成可加工 CAD",
        },
        {
            "type": "generate_quote",
            "label": "生成报价",
            "description": "基于三维方案报价",
        },
        {
            "type": "generate_package",
            "label": "生成工程包",
            "description": "输出 ZIP / PDF / BOM 全套",
        },
    ],
    "cad": [
        {
            "type": "generate_quote",
            "label": "生成报价",
            "description": "基于 CAD 方案报价",
        },
        {
            "type": "generate_package",
            "label": "生成工程包",
            "description": "打包 ZIP / PDF / BOM",
        },
    ],
    "quote": [
        {
            "type": "generate_package",
            "label": "生成工程包",
            "description": "把设计与报价打包交付",
        },
    ],
}


class NextActionAgent:
    def create_next_action(
        self,
        *,
        db: Session,
        user_id: str,
        conversation_id: UUID,
        project_id: str | None,
        parent_id: UUID,
        source_node_type: str,
    ) -> WorkspaceNode | None:
        recs = list(_NEXT_STEPS.get(source_node_type) or [])
        if not recs:
            return None
        # 已有同类型 next_action 子节点时跳过，避免重复。
        existing = [
            n
            for n in workspace_graph_service.get_nodes(
                db, user_id=user_id, conversation_id=conversation_id
            )
            if n.node_type == "next_action" and n.parent_id == parent_id
        ]
        if existing:
            return None
        node = workspace_graph_service.create_node(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=project_id,
            parent_id=parent_id,
            node_type="next_action",
            status="waiting_user",
            title="下一步",
            summary="选择下一步继续推进：",
            agent_key="next_action_agent",
            input_data={"sourceNodeType": source_node_type},
            output_data={"recommendations": recs},
        )
        return node


next_action_agent = NextActionAgent()