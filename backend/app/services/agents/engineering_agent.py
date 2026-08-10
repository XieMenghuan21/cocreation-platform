"""EngineeringAgent：把现有 engineering_package_service 变成 Workspace 节点。

不重写工程包服务。Agent 负责读取最新可用模型/CAD 任务 → build_package →
创建 engineering_package 节点并挂上 asset；若缺少模型则返回 next_action 提示。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.persistence import Asset, WorkflowTask
from app.models.quote import QuoteLineItem, QuoteRecord
from app.models.workspace_node import WorkspaceNode
from app.services.engineering_package_service import (
    EngineeringPackageServiceError,
    engineering_package_service,
)
from app.services.workspace_graph_service import workspace_graph_service

logger = logging.getLogger(__name__)


class EngineeringAgentError(Exception):
    pass


ASSET_OUTPUT_KEYS = (
    "modelStepAssetId",
    "modelStlAssetId",
    "modelGlbAssetId",
    "modelScriptAssetId",
    "renderPngAssetId",
    "enhancedImageAssetId",
    "drawingAssetId",
    "planLineSvgAssetId",
    "planLineDxfAssetId",
)


def _decimal_to_float(value: Decimal | int | float | None) -> float | None:
    if value is None:
        return None
    return float(value)


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

    def _latest_quote(
        self,
        db: Session,
        *,
        user_id: str,
        project_id: str | None,
    ) -> QuoteRecord | None:
        """找当前项目最新正式报价。工程包必须绑定报价/BOM。"""
        query = select(QuoteRecord).where(QuoteRecord.user_id == user_id)
        if project_id:
            query = query.where(QuoteRecord.project_id == project_id)
        query = query.order_by(QuoteRecord.created_at.desc())
        return db.scalars(query).first()

    def _quote_line_item_ids(
        self,
        db: Session,
        *,
        quote: QuoteRecord,
    ) -> list[int]:
        query = (
            select(QuoteLineItem.id)
            .where(
                QuoteLineItem.quote_id == quote.id,
                QuoteLineItem.user_id == quote.user_id,
                QuoteLineItem.project_id == quote.project_id,
            )
            .order_by(QuoteLineItem.id.asc())
        )
        return [int(item_id) for item_id in db.scalars(query)]

    def _included_asset_ids(self, task: WorkflowTask) -> list[str]:
        """从任务输出中提取工程包来源资产，保持顺序去重。"""
        outputs = task.outputs or {}
        seen: set[str] = set()
        asset_ids: list[str] = []

        def add(value: object) -> None:
            if isinstance(value, UUID):
                text = str(value)
            elif isinstance(value, str):
                text = value.strip()
            else:
                return
            if not text or text in seen:
                return
            seen.add(text)
            asset_ids.append(text)

        for key in ASSET_OUTPUT_KEYS:
            add(outputs.get(key))

        render_views = outputs.get("renderViews")
        if isinstance(render_views, Iterable) and not isinstance(render_views, (str, bytes, dict)):
            for view in render_views:
                if isinstance(view, dict):
                    add(view.get("assetId"))

        return asset_ids

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
        quote = self._latest_quote(db, user_id=user_id, project_id=project_id)
        if quote is None:
            return None, "当前还缺少正式报价/BOM，建议先生成报价，再生成工程包。"

        bom_line_item_ids = self._quote_line_item_ids(db, quote=quote)
        included_asset_ids = self._included_asset_ids(task)

        node = workspace_graph_service.create_node(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=project_id,
            parent_id=source_node.id,
            node_type="engineering_package",
            status="running",
            title=f"工程包「{project_name}」",
            summary=(
                f"基于任务 {task.id}、报价 {quote.id} 组装 ZIP/PDF/BOM，"
                f"包含 {len(included_asset_ids)} 个来源资产。"
            ),
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
                asset_uuid = _UUID(str(package_asset_id))
                asset_exists = db.get(Asset, asset_uuid) is not None
                if asset_exists:
                    workspace_graph_service.link_asset(
                        db,
                        node_id=node.id,
                        asset_id=asset_uuid,
                        role="package",
                    )
                else:
                    logger.warning("工程包 asset 不存在，跳过节点资产关联: %s", package_asset_id)
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
                "sourceTaskId": task.id,
                "quoteRecordId": str(quote.id),
                "bomLineItemIds": bom_line_item_ids,
                "includedAssetIds": included_asset_ids,
                "pricingSource": quote.pricing_source,
                "finalQuote": _decimal_to_float(quote.final_quote),
                "currency": quote.currency,
            },
        ) or node
        return node, f"工程包已生成：{result.get('filename') or '工程包.zip'}"


engineering_agent = EngineeringAgent()
