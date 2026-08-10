"""QuoteAgent：把报价变成 Workspace 中的 quote 节点。

第一版不做复杂算法：基于设计方向/需求给出估算区间与假设，重点是「报价进入 Workspace」。
"""
from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.workspace_node import WorkspaceNode
from app.services.quote_service import QuoteInput, QuoteLineInput, quote_service
from app.services.workspace_graph_service import workspace_graph_service

logger = logging.getLogger(__name__)


class QuoteAgentError(Exception):
    pass


class QuoteAgent:
    """生成 quote 节点并返回结构化报价数据。"""

    def build_quote(
        self,
        *,
        node: WorkspaceNode,
        project_name: str,
        direction_title: str = "",
    ) -> dict[str, object]:
        """根据需求信息计算一个可解释的估算报价。"""
        requirement = str(node.summary or "")
        complexity = 1.0
        if any(k in requirement for k in ("复杂", "精密", "多组件", "折叠")):
            complexity = 1.3
        elif any(k in requirement for k in ("简单", "基础", "轻量")):
            complexity = 0.85

        base_design = 1200.0
        modeling = 800.0 * complexity
        render = 300.0
        est_min = round((base_design + modeling + render) / 100) * 100
        est_max = round(est_min * 1.25 / 100) * 100

        return {
            "currency": "CNY",
            "range": {"min": est_min, "max": est_max},
            "breakdown": [
                {"label": "设计深化", "amount": round(base_design / 100) * 100},
                {"label": "三维/CAD", "amount": round(modeling / 100) * 100},
                {"label": "效果图渲染", "amount": render},
            ],
            "assumptions": [
                "当前为设计服务估算，不是量产采购报价",
                "最终价格以确认的设计方案与加工工艺为准",
            ],
            "directionTitle": direction_title,
        }

    def create_quote_node(
        self,
        *,
        db: Session,
        user_id: str,
        conversation_id: UUID,
        project_id: str | None,
        project_name: str,
        source_node: WorkspaceNode,
    ) -> WorkspaceNode:
        """创建 quote 节点（completed）。"""
        if not project_id:
            raise QuoteAgentError("报价必须绑定项目")
        quote = self.build_quote(
            node=source_node,
            project_name=project_name,
            direction_title=source_node.title,
        )
        node = workspace_graph_service.create_node(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=project_id,
            parent_id=source_node.id,
            node_type="quote",
            status="completed",
            title=f"报价「{project_name}」",
            summary=(
                f"预计 {quote['range']['min']} ~ {quote['range']['max']} {quote['currency']}"
            ),
            agent_key="quote_agent",
            input_data={
                "directionTitle": source_node.title,
                "requirement": source_node.summary,
            },
            output_data=quote,
        )
        workflow_id_value = node.ui_data.get("workflowId") if isinstance(node.ui_data, dict) else None
        if not isinstance(workflow_id_value, str) or not workflow_id_value:
            raise QuoteAgentError("报价节点缺少工作流记录")
        record = quote_service.create_quote(
            db,
            QuoteInput(
                user_id=user_id,
                project_id=project_id,
                workflow_id=UUID(workflow_id_value),
                quantity=1,
                material_lines=(
                    QuoteLineInput(
                        category="material",
                        name="主体材料",
                        unit="项",
                        quantity=1,
                        unit_price=Decimal("1200"),
                        note="基于当前需求的默认材料估算",
                    ),
                ),
                process_lines=(
                    QuoteLineInput(
                        category="process",
                        name="设计深化与工艺评估",
                        unit="项",
                        quantity=1,
                        unit_price=Decimal("800"),
                    ),
                    QuoteLineInput(
                        category="process",
                        name="渲染与展示图",
                        unit="项",
                        quantity=1,
                        unit_price=Decimal("300"),
                    ),
                ),
                labor_lines=(
                    QuoteLineInput(
                        category="labor",
                        name="方案整理",
                        unit="项",
                        quantity=1,
                        unit_price=Decimal("200"),
                    ),
                ),
                loss_rate=Decimal("0.08"),
                overhead_rate=Decimal("0.12"),
                margin_rate=Decimal("0.25"),
                input_snapshot={
                    "projectName": project_name,
                    "directionTitle": source_node.title,
                    "requirement": source_node.summary,
                    "sourceNodeId": str(source_node.id),
                },
            ),
        )
        output = dict(node.output_data or {})
        output.update(
            {
                "quoteRecordId": str(record.id),
                "pricingSource": record.pricing_source,
                "materialCost": float(record.material_cost),
                "productionCost": float(record.process_cost + record.labor_cost),
                "totalInternal": float(record.subtotal),
                "totalCustomer": float(record.final_quote),
                "range": {
                    "min": float(record.subtotal),
                    "max": float(record.final_quote),
                },
            }
        )
        node = workspace_graph_service.update_node(
            db,
            node_id=node.id,
            user_id=user_id,
            status="completed",
            output_data=output,
        ) or node
        return node


quote_agent = QuoteAgent()
