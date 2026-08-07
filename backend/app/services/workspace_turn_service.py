"""Conversation Turn 编排服务：用户每说一句话 → Orchestrator → Workspace Graph。

Day1 范围：
- 首轮：意图识别 → 自动创建 Project → ProjectNode + RequirementNode
- 后续：文本 / 卡片动作都走同一入口，识别 waiting_user 节点并推进
- Render / CAD / 3D / Quote / Package 由 Day2 的 Agent 接入
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, ConversationMessage
from app.models.workspace_node import WorkspaceNode
from app.schemas.workspace_graph import TurnRequest, TurnResponse
from app.services.cocreation_history_service import cocreation_history_service
from app.services.intent_service import IntentServiceError, intent_service
from app.services.workspace_graph_service import workspace_graph_service

logger = logging.getLogger(__name__)


class WorkspaceTurnError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class WorkspaceTurnService:
    def _get_or_create_conversation(
        self,
        db: Session,
        *,
        user_id: str,
        conversation_id: UUID | None,
    ) -> Conversation:
        if conversation_id is not None:
            conversation = db.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            if conversation is not None:
                return conversation
        conversation = Conversation(
            user_id=user_id,
            project_id=None,
            title="新对话",
        )
        db.add(conversation)
        db.flush()
        return conversation

    @staticmethod
    def _save_user_message(
        db: Session,
        conversation: Conversation,
        *,
        text: str,
        asset_ids: list[str],
        action: dict[str, object] | None,
    ) -> ConversationMessage:
        message = ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            text=text or "",
            card_data={
                "assetIds": asset_ids,
                "action": action,
            },
        )
        db.add(message)
        conversation.updated_at = datetime.now(timezone.utc)
        if conversation.title == "新对话" and text.strip():
            conversation.title = text.strip()[:40]
        db.flush()
        return message

    @staticmethod
    def _save_assistant_message(
        db: Session,
        conversation: Conversation,
        *,
        text: str,
        card_data: dict[str, object] | None = None,
    ) -> ConversationMessage:
        message = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            text=text,
            card_data=card_data or {},
        )
        db.add(message)
        conversation.updated_at = datetime.now(timezone.utc)
        db.flush()
        return message

    async def handle_turn(
        self,
        db: Session,
        *,
        auth_user: dict[str, object],
        user_id: str,
        conversation_id: UUID | None,
        request: TurnRequest,
    ) -> TurnResponse:
        conversation = self._get_or_create_conversation(
            db, user_id=user_id, conversation_id=conversation_id
        )
        text = (request.text or "").strip()
        action = request.action

        if action and action.get("nodeId"):
            return await self._handle_action(
                db, conversation, user_id, text, action
            )
        if not text:
            raise WorkspaceTurnError("本轮输入为空，请提供文本或卡片动作", 400)

        return await self._handle_text(
            db, conversation, auth_user, user_id, text, request.asset_ids
        )

    async def _handle_text(
        self,
        db: Session,
        conversation: Conversation,
        auth_user: dict[str, object],
        user_id: str,
        text: str,
        asset_ids: list[str],
    ) -> TurnResponse:
        self._save_user_message(db, conversation, text=text, asset_ids=asset_ids, action=None)

        nodes = workspace_graph_service.get_nodes(db, user_id=user_id, conversation_id=conversation.id)
        has_project = any(n.node_type == "project" for n in nodes)
        waiting = [n for n in nodes if n.status == "waiting_user"]

        created: list[WorkspaceNode] = []
        updated: list[WorkspaceNode] = []

        if not has_project:
            created, updated, assistant_text = await self._first_turn_project_creation(
                db, conversation, auth_user, user_id, text, asset_ids, created, updated
            )
        elif waiting:
            assistant_text = await self._push_waiting_node(
                db, conversation, user_id, text, waiting, created, updated
            )
        else:
            # 已有项目但没有等待用户输入：把新文本补进需求，追加 requirement 摘要
            requirement_nodes = [n for n in nodes if n.node_type == "requirement"]
            if requirement_nodes:
                node = requirement_nodes[-1]
                merged = f"{node.summary}\n补充：{text}".strip()
                workspace_graph_service.update_node(
                    db, node_id=node.id, user_id=user_id, summary=merged
                )
                updated.append(node)
            assistant_text = "已记录你的补充信息。需要我继续生成设计方案，还是推进 3D / CAD / 报价？"

        assistant = self._save_assistant_message(
            db,
            conversation,
            text=assistant_text,
            card_data={"nodes": [str(n.id) for n in (created + updated)]},
        )
        db.commit()

        return TurnResponse(
            conversation_id=conversation.id,
            message={"id": assistant.id, "role": "assistant", "text": assistant_text},
            nodes_created=[workspace_graph_service.to_data(n) for n in created],
            nodes_updated=[workspace_graph_service.to_data(n) for n in updated],
            workspace={
                "activeNodeId": str(created[-1].id) if created else None,
                "previewNodeId": None,
            },
        )

    async def _first_turn_project_creation(
        self,
        db: Session,
        conversation: Conversation,
        auth_user: dict[str, object],
        user_id: str,
        text: str,
        asset_ids: list[str],
        created: list[WorkspaceNode],
        updated: list[WorkspaceNode],
    ) -> tuple[list[WorkspaceNode], list[WorkspaceNode], str]:
        try:
            intent = await intent_service.analyze(text)
        except IntentServiceError as exc:
            raise WorkspaceTurnError(f"意图识别失败：{exc.message}", exc.status_code) from exc

        project_name = str(intent.get("projectName") or text[:20])
        project_payload = cocreation_history_service.create_project(
            db,
            auth_user=auth_user,
            name=project_name,
            description=str(intent.get("requirementText") or text),
            industry=str(intent.get("industry") or "装备制造"),
            input_mode="prompt",
        )
        project_id = project_payload.get("id") or project_payload.get("projectId") or ""

        project_node = workspace_graph_service.create_node(
            db,
            user_id=user_id,
            conversation_id=conversation.id,
            node_type="project",
            status="completed",
            title=project_name,
            summary=str(intent.get("requirementText") or text),
            project_id=project_id,
            agent_key="project_agent",
            input_data={"intent": intent},
        )
        created.append(project_node)

        requirement_node = workspace_graph_service.create_node(
            db,
            user_id=user_id,
            conversation_id=conversation.id,
            node_type="requirement",
            status="waiting_user" if intent.get("needsMaterials") else "draft",
            title="需求理解",
            summary=str(intent.get("requirementText") or text),
            project_id=project_id,
            parent_id=project_node.id,
            agent_key="requirement_agent",
            input_data={"intent": intent},
        )
        created.append(requirement_node)

        conversation.project_id = project_id
        db.flush()

        needs = bool(intent.get("needsMaterials"))
        assistant_text = (
            f"已为你建立项目「{project_name}」。\n"
            f"初步理解：{intent.get('requirementText') or text}\n"
            f"{'需要补充材质 / 尺寸 / 使用场景等信息来锁定设计方向。' if needs else '信息已足够，可以进入设计方向。'}"
        )
        return created, updated, assistant_text

    async def _push_waiting_node(
        self,
        db: Session,
        conversation: Conversation,
        user_id: str,
        text: str,
        waiting: list[WorkspaceNode],
        created: list[WorkspaceNode],
        updated: list[WorkspaceNode],
    ) -> str:
        """把用户的补充文本回填到最早的 waiting_user 节点。"""
        node = waiting[0]
        prev_summary = node.summary
        merged = f"{prev_summary}\n补充：{text}".strip()
        node = workspace_graph_service.update_node(
            db, node_id=node.id, user_id=user_id, summary=merged
        ) or node
        updated.append(node)
        return (
            "已补充到需求。还需要更多信息，还是直接开始生成设计方向？"
        )

    async def _handle_action(
        self,
        db: Session,
        conversation: Conversation,
        user_id: str,
        text: str,
        action: dict[str, object],
    ) -> TurnResponse:
        node_id_raw = str(action.get("nodeId") or "")
        action_type = str(action.get("type") or "")
        try:
            node_id = UUID(node_id_raw)
        except ValueError as exc:
            raise WorkspaceTurnError("节点 ID 无效", 400) from exc

        node = workspace_graph_service.get_node(db, user_id=user_id, node_id=node_id)
        if node is None:
            raise WorkspaceTurnError("节点不存在或不属于当前用户", 404)

        self._save_user_message(db, conversation, text=text, asset_ids=[], action=action)

        updated: list[WorkspaceNode] = []
        assistant_text = "收到。"
        if action_type in {"confirm", "complete", "accept"}:
            node = workspace_graph_service.update_node(
                db, node_id=node.id, user_id=user_id, status="completed"
            ) or node
            updated.append(node)
            if node.node_type == "requirement":
                assistant_text = "需求已确认。接下来我会先生成 A / B / C 三个设计方向供你选择。"
            elif node.node_type == "design_direction":
                siblings = workspace_graph_service.supersede_siblings(
                    db,
                    user_id=user_id,
                    node_id=node.id,
                    node_type="design_direction",
                    conversation_id=conversation.id,
                )
                updated.extend(siblings)
                assistant_text = f"已选定设计方向「{node.title}」。开始生成渲染方案。"
            else:
                assistant_text = f"「{node.title}」已确认。"
        elif action_type in {"select", "choose"}:
            node = workspace_graph_service.update_node(
                db, node_id=node.id, user_id=user_id, status="completed"
            ) or node
            updated.append(node)
            siblings = workspace_graph_service.supersede_siblings(
                db,
                user_id=user_id,
                node_id=node.id,
                node_type=node.node_type,
                conversation_id=conversation.id,
            )
            updated.extend(siblings)
            assistant_text = f"已选择「{node.title}」。"
        elif action_type == "request":
            node = workspace_graph_service.update_node(
                db, node_id=node.id, user_id=user_id, status="queued"
            ) or node
            updated.append(node)
            assistant_text = f"已提交「{node.title}」任务，正在排队执行。"
        else:
            raise WorkspaceTurnError(f"不支持的动作类型：{action_type}", 400)

        assistant = self._save_assistant_message(
            db,
            conversation,
            text=assistant_text,
            card_data={"nodes": [str(n.id) for n in updated]},
        )
        db.commit()

        return TurnResponse(
            conversation_id=conversation.id,
            message={"id": assistant.id, "role": "assistant", "text": assistant_text},
            nodes_created=[],
            nodes_updated=[workspace_graph_service.to_data(n) for n in updated],
            workspace={
                "activeNodeId": str(node.id) if node else None,
                "previewNodeId": str(node.id) if node and node.node_type in {"render", "model_3d", "cad"} else None,
            },
        )


workspace_turn_service = WorkspaceTurnService()
