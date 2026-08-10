"""Conversation Turn 编排服务：用户每说一句话 → Orchestrator → Workspace Graph。

Day1 范围：
- 首轮：意图识别 → 自动创建 Project → ProjectNode + RequirementNode
- 后续：文本 / 卡片动作都走同一入口，识别 waiting_user 节点并推进
- Render / CAD / 3D / Quote / Package 由 Day2 的 Agent 接入
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, ConversationMessage
from app.models.workspace_node import WorkspaceNode
from app.schemas.workspace_graph import TurnRequest, TurnResponse
from app.services.agents.cad_agent import CadAgentError, cad_agent
from app.services.agents.design_agent import DesignAgentError, design_agent
from app.services.agents.engineering_agent import (
    EngineeringAgentError,
    engineering_agent,
)
from app.services.agents.model_agent import ModelAgentError, model_agent
from app.services.agents.quote_agent import quote_agent
from app.services.agents.render_agent import render_agent
from app.services.agents.requirement_agent import requirement_agent
from app.services.cocreation_history_service import cocreation_history_service
from app.services.intent_service import IntentServiceError, intent_service
from app.services.workspace_graph_service import workspace_graph_service

if TYPE_CHECKING:
    from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def _extract_industry(project_node: WorkspaceNode | None) -> str:
    if not project_node or not isinstance(project_node.input_data, dict):
        return "装备制造"
    intent = project_node.input_data.get("intent")
    if isinstance(intent, dict):
        return str(intent.get("industry") or "装备制造")
    return "装备制造"


def _string_from_bucket(bucket: dict[str, object] | None, key: str) -> str:
    if not isinstance(bucket, dict):
        return ""
    value = bucket.get(key)
    return value.strip() if isinstance(value, str) else ""


def _promotion_source_metadata(node: WorkspaceNode) -> tuple[str, str]:
    source_image_url = (
        _string_from_bucket(node.output_data, "sourceImageUrl")
        or _string_from_bucket(node.ui_data, "sourceImageUrl")
        or _string_from_bucket(node.input_data, "referenceImageUrl")
    )
    source_node_title = (
        _string_from_bucket(node.output_data, "sourceNodeTitle")
        or _string_from_bucket(node.ui_data, "sourceNodeTitle")
        or "设计图"
    )
    return source_image_url, source_node_title


class WorkspaceTurnError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class WorkspaceTurnService:
    def __init__(self) -> None:
        self._pending_launches: list[dict[str, object]] = []

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
            raise WorkspaceTurnError("会话不存在", 404)
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

        self._pending_launches = []

        if action and action.get("nodeId"):
            result = await self._handle_action(
                db, conversation, auth_user, user_id, text, action
            )
        elif not text:
            raise WorkspaceTurnError("本轮输入为空，请提供文本或卡片动作", 400)
        else:
            result = await self._handle_text(
                db, conversation, auth_user, user_id, text, request.asset_ids
            )

        for pending in self._pending_launches:
            kind = str(pending.get("kind") or "")
            if kind == "render":
                asyncio.create_task(
                    self._launch_render_post_commit(
                        auth_user=auth_user,
                        pending=pending,
                    )
                )
            elif kind == "model_3d":
                asyncio.create_task(
                    self._launch_model_post_commit(
                        auth_user=auth_user,
                        pending=pending,
                    )
                )
            elif kind == "cad":
                asyncio.create_task(
                    self._launch_cad_post_commit(
                        auth_user=auth_user,
                        pending=pending,
                    )
                )
        self._pending_launches = []

        return result

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
            # 已有项目但没有 waiting_user：自然语言仍然先交给 RequirementAgent。
            requirement_nodes = [n for n in nodes if n.node_type == "requirement"]
            if requirement_nodes:
                node = requirement_nodes[-1]
                previous: dict[str, object] = {}
                if isinstance(node.input_data, dict):
                    value = node.input_data.get("requirement")
                    if isinstance(value, dict):
                        previous = value
                analysis = await requirement_agent.analyze(
                    previous=previous,
                    latest=text,
                    intent=(node.input_data or {}).get("intent") if isinstance(node.input_data, dict) else None,
                )
                node = workspace_graph_service.update_node(
                    db,
                    node_id=node.id,
                    user_id=user_id,
                    status="waiting_user",
                    summary=str(analysis.get("summary") or text),
                    input_data={**(node.input_data or {}), "requirement": analysis.get("requirement") or {}},
                    output_data={
                        **(node.output_data or {}),
                        "completeness": analysis.get("completeness"),
                        "criticalUnknown": analysis.get("criticalUnknown"),
                        "question": analysis.get("question"),
                        "canProceed": analysis.get("canProceed"),
                    },
                    ui_data={
                        **(node.ui_data or {}),
                        "completeness": analysis.get("completeness"),
                        "question": analysis.get("question"),
                        "canProceed": analysis.get("canProceed"),
                    },
                ) or node
                updated.append(node)
                assistant_text = (
                    "我已经把这次修改合并进需求。当前信息足够继续设计；确认需求后，我会重新生成设计方向。"
                    if analysis.get("canProceed")
                    else str(analysis.get("question") or "我已经记录修改。再补充一个关键信息后就可以继续设计。")
                )
            else:
                assistant_text = "已记录你的补充信息。"

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

        requirement_analysis = await requirement_agent.analyze(
            previous={},
            latest=text,
            intent=intent,
        )
        requirement_node = workspace_graph_service.create_node(
            db,
            user_id=user_id,
            conversation_id=conversation.id,
            node_type="requirement",
            status="waiting_user",
            title="需求理解",
            summary=str(requirement_analysis.get("summary") or intent.get("requirementText") or text),
            project_id=project_id,
            parent_id=project_node.id,
            agent_key="requirement_agent",
            input_data={
                "intent": intent,
                "requirement": requirement_analysis.get("requirement") or {},
                "assetIds": asset_ids,
            },
            output_data={
                "completeness": requirement_analysis.get("completeness"),
                "criticalUnknown": requirement_analysis.get("criticalUnknown"),
                "question": requirement_analysis.get("question"),
                "canProceed": requirement_analysis.get("canProceed"),
            },
            ui_data={
                "completeness": requirement_analysis.get("completeness"),
                "question": requirement_analysis.get("question"),
                "canProceed": requirement_analysis.get("canProceed"),
            },
        )
        created.append(requirement_node)

        conversation.project_id = project_id
        conversation.title = project_name
        db.flush()

        next_text = (
            "需求已经足够开始设计。确认后我会生成 3 个差异化设计方向。"
            if requirement_analysis.get("canProceed")
            else str(requirement_analysis.get("question") or "还需要确认一个关键方向。")
        )
        assistant_text = (
            f"已为你建立项目「{project_name}」。\n"
            f"我对当前需求的理解：{requirement_node.summary}\n"
            f"{next_text}"
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
        """把用户补充交给 waiting_user 节点；Requirement 由 RequirementAgent 结构化合并。"""
        node = waiting[0]
        if node.node_type == "requirement":
            previous: dict[str, object] = {}
            if isinstance(node.input_data, dict):
                value = node.input_data.get("requirement")
                if isinstance(value, dict):
                    previous = value
            analysis = await requirement_agent.analyze(
                previous=previous,
                latest=text,
                intent=(node.input_data or {}).get("intent") if isinstance(node.input_data, dict) else None,
            )
            node = workspace_graph_service.update_node(
                db,
                node_id=node.id,
                user_id=user_id,
                summary=str(analysis.get("summary") or text),
                input_data={**(node.input_data or {}), "requirement": analysis.get("requirement") or {}},
                output_data={
                    **(node.output_data or {}),
                    "completeness": analysis.get("completeness"),
                    "criticalUnknown": analysis.get("criticalUnknown"),
                    "question": analysis.get("question"),
                    "canProceed": analysis.get("canProceed"),
                },
                ui_data={
                    **(node.ui_data or {}),
                    "completeness": analysis.get("completeness"),
                    "question": analysis.get("question"),
                    "canProceed": analysis.get("canProceed"),
                },
            ) or node
            updated.append(node)
            if analysis.get("canProceed"):
                return "需求已经整理到可以开始设计的程度。你可以直接确认，我会生成 3 个设计方向；也可以继续补充。"
            return str(analysis.get("question") or "我已经记录。再补充一个最关键的信息后就可以开始设计。")

        prev_summary = node.summary
        merged = f"{prev_summary}\n补充：{text}".strip()
        node = workspace_graph_service.update_node(
            db, node_id=node.id, user_id=user_id, summary=merged
        ) or node
        updated.append(node)
        return "已记录。"

    async def _handle_action(
        self,
        db: Session,
        conversation: Conversation,
        auth_user: dict[str, object],
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

        created: list[WorkspaceNode] = []
        updated: list[WorkspaceNode] = []
        assistant_text = "收到。"

        all_nodes = workspace_graph_service.get_nodes(
            db, user_id=user_id, conversation_id=conversation.id
        )
        project_node = next((n for n in all_nodes if n.node_type == "project"), None)
        project_name = project_node.title if project_node else conversation.title or "未命名项目"
        project_id = project_node.project_id if project_node else node.project_id or conversation.project_id

        if action_type in {"confirm", "complete", "accept"}:
            node = workspace_graph_service.update_node(
                db, node_id=node.id, user_id=user_id, status="completed"
            ) or node
            updated.append(node)

            if node.node_type == "requirement":
                requirement_text = node.summary
                try:
                    directions = await design_agent.generate_directions(
                        requirement=requirement_text,
                        project_name=project_name,
                    )
                except DesignAgentError as exc:
                    raise WorkspaceTurnError(f"设计方向生成失败：{exc}", 502) from exc

                for d in directions:
                    dn = workspace_graph_service.create_node(
                        db,
                        user_id=user_id,
                        conversation_id=conversation.id,
                        node_type="design_direction",
                        status="waiting_user",
                        title=f"{d['name']}（{d['key']}）",
                        summary=d.get("summary") or "",
                        project_id=project_id,
                        parent_id=node.id,
                        agent_key="design_agent",
                        input_data=dict(d),
                        output_data={},
                        ui_data={
                            "directionKey": d.get("key"),
                            "styleKeywords": d.get("styleKeywords"),
                            "cmf": d.get("cmf"),
                            "imagePrompt": d.get("imagePrompt"),
                        },
                    )
                    created.append(dn)

                assistant_text = (
                    f"已为你生成 {len(directions)} 个差异化设计方向，请选择一个：\n"
                    + "\n".join(
                        f"**{d['key']}** — {d['name']}：{d.get('summary', '')}"
                        for d in directions
                    )
                )

            elif node.node_type == "design_direction":
                siblings = workspace_graph_service.supersede_siblings(
                    db,
                    user_id=user_id,
                    node_id=node.id,
                    node_type="design_direction",
                    conversation_id=conversation.id,
                )
                updated.extend(siblings)

                direction_data = dict(node.input_data) if node.input_data else {}
                direction_image_prompt = direction_data.get(
                    "imagePrompt", ""
                ) or node.summary

                render_node = workspace_graph_service.create_node(
                    db,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    node_type="render",
                    status="queued",
                    title=f"渲染方案「{project_name}」",
                    summary=direction_image_prompt or node.summary,
                    project_id=project_id,
                    parent_id=node.id,
                    agent_key="render_agent",
                    input_data={
                        "requirement": node.summary,
                        "imagePrompt": direction_image_prompt or "",
                    },
                )
                created.append(render_node)

                self._pending_launches.append(
                    {
                        "kind": "render",
                        "node_id": str(render_node.id),
                        "user_id": user_id,
                        "conversation_id": str(conversation.id),
                        "project_name": project_name,
                        "requirement_text": node.summary,
                        "direction_image_prompt": direction_image_prompt,
                        "industry": _extract_industry(project_node),
                    }
                )

                assistant_text = f"已选定设计方向「{node.title}」。渲染任务已提交，正在排队生成。可在右侧预览区查看进度。"

            else:
                assistant_text = f"「{node.title}」已确认。"

        elif action_type == "generate_3d":
            model_node = workspace_graph_service.create_node(
                db,
                user_id=user_id,
                conversation_id=conversation.id,
                node_type="model_3d",
                status="queued",
                title=f"3D 模型「{project_name}」",
                summary=node.summary or project_name,
                project_id=project_id,
                parent_id=node.id,
                agent_key="model_agent",
                input_data={
                    "requirement": node.summary,
                },
            )
            created.append(model_node)
            self._pending_launches.append(
                {
                    "kind": "model_3d",
                    "node_id": str(model_node.id),
                    "user_id": user_id,
                    "conversation_id": str(conversation.id),
                    "project_name": project_name,
                    "requirement_text": node.summary,
                    "industry": _extract_industry(project_node),
                }
            )
            assistant_text = f"已提交 3D 建模任务「{model_node.title}」，正在排队执行。完成后可在右侧预览 3D 模型。"

        elif action_type == "generate_cad":
            cad_node = workspace_graph_service.create_node(
                db,
                user_id=user_id,
                conversation_id=conversation.id,
                node_type="cad",
                status="queued",
                title=f"CAD 图纸「{project_name}」",
                summary=node.summary or project_name,
                project_id=project_id,
                parent_id=node.id,
                agent_key="cad_agent",
                input_data={
                    "requirement": node.summary,
                },
            )
            created.append(cad_node)
            self._pending_launches.append(
                {
                    "kind": "cad",
                    "node_id": str(cad_node.id),
                    "user_id": user_id,
                    "conversation_id": str(conversation.id),
                    "project_name": project_name,
                    "requirement_text": node.summary,
                    "industry": _extract_industry(project_node),
                }
            )
            assistant_text = f"已提交 CAD 生成任务「{cad_node.title}」，正在排队执行。"

        elif action_type == "generate_quote":
            quote_node = quote_agent.create_quote_node(
                db=db,
                user_id=user_id,
                conversation_id=conversation.id,
                project_id=project_id,
                project_name=project_name,
                source_node=node,
            )
            created.append(quote_node)
            assistant_text = (
                f"已生成估算报价：{quote_node.output_data.get('range', {}).get('min')} ~ "
                f"{quote_node.output_data.get('range', {}).get('max')} CNY。"
                "如需工程包，可继续生成。"
            )

        elif action_type == "generate_package":
            try:
                package_node, package_text = engineering_agent.build_package_node(
                    db,
                    auth_user=auth_user,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    project_id=project_id,
                    project_name=project_name,
                    source_node=node,
                )
            except EngineeringAgentError as exc:
                raise WorkspaceTurnError(f"工程包生成失败：{exc}", 502) from exc
            if package_node is not None:
                created.append(package_node)
                assistant_text = package_text
            else:
                assistant_text = (
                    package_text
                    + "\n建议先点击「生成 3D 模型」或「生成 CAD」建立可打包的模型结果。"
                )

        elif action_type in {"regenerate_scene", "adjust_style"}:
            source_image_url, source_node_title = _promotion_source_metadata(node)
            if not source_image_url:
                raise WorkspaceTurnError(
                    "请先生成或选择一张设计图，宣发图必须基于设计图进行图片编辑。",
                    400,
                )
            action_prompt = (
                "沿用同一张设计图，换一个更适合商业展示的宣发场景。"
                if action_type == "regenerate_scene"
                else "沿用同一张设计图，调整宣发图的光线、质感和整体风格。"
            )
            render_node = workspace_graph_service.create_node(
                db,
                user_id=user_id,
                conversation_id=conversation.id,
                node_type="render",
                status="queued",
                title=f"宣发图「{project_name}」",
                summary=action_prompt,
                project_id=project_id,
                parent_id=node.id,
                agent_key="render_agent",
                input_data={
                    "requirement": node.summary,
                    "imagePrompt": action_prompt,
                    "referenceImageUrl": source_image_url,
                },
                output_data={
                    "renderMode": "promotion",
                    "sourceImageUrl": source_image_url,
                    "sourceNodeTitle": source_node_title,
                },
                ui_data={
                    "renderMode": "promotion",
                    "sourceImageUrl": source_image_url,
                    "sourceNodeTitle": source_node_title,
                },
            )
            created.append(render_node)
            self._pending_launches.append(
                {
                    "kind": "render",
                    "node_id": str(render_node.id),
                    "user_id": user_id,
                    "conversation_id": str(conversation.id),
                    "project_name": project_name,
                    "requirement_text": node.summary,
                    "direction_image_prompt": action_prompt,
                    "industry": _extract_industry(project_node),
                }
            )
            assistant_text = (
                f"已基于同一张设计图「{source_node_title}」提交新的宣发图图片编辑任务。"
            )

        elif action_type == "generate_copy":
            copy_node = workspace_graph_service.create_node(
                db,
                user_id=user_id,
                conversation_id=conversation.id,
                node_type="status",
                status="completed",
                title=f"海报文案「{project_name}」",
                summary=(
                    f"{project_name}\n"
                    "核心卖点：延续当前设计语言，突出材质质感、使用场景与产品价值。\n"
                    "建议标题：让设计进入真实生活场景。"
                ),
                project_id=project_id,
                parent_id=node.id,
                input_data={"sourceNodeId": str(node.id)},
                output_data={
                    "copyType": "poster",
                    "headline": "让设计进入真实生活场景",
                    "sellingPoints": ["材质质感", "场景融合", "产品价值"],
                },
            )
            created.append(copy_node)
            assistant_text = "已生成一版海报文案，并保存到当前项目工作节点。"

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
            card_data={"nodes": [str(n.id) for n in (created + updated)]},
        )
        db.commit()

        return TurnResponse(
            conversation_id=conversation.id,
            message={"id": assistant.id, "role": "assistant", "text": assistant_text},
            nodes_created=[workspace_graph_service.to_data(n) for n in created],
            nodes_updated=[workspace_graph_service.to_data(n) for n in updated],
            workspace={
                "activeNodeId": str(created[-1].id) if created else str(node.id),
                "previewNodeId": str(created[-1].id) if created and created[-1].node_type in {"render", "model_3d", "cad"} else None,
            },
        )

    async def _launch_render_post_commit(
        self,
        *,
        auth_user: dict[str, object],
        pending: dict[str, object],
    ) -> None:
        """在 turn 提交后，用独立 DB 会话启动渲染工作流。"""
        from uuid import UUID as _UUID

        from app.db.session import SessionLocal as _SessionLocal
        from app.models.workspace_node import WorkspaceNode as _WorkspaceNode
        from app.services.agents.render_agent import render_agent as _render_agent

        try:
            db = _SessionLocal()
            try:
                node_id = _UUID(str(pending["node_id"]))
                node = db.scalar(
                    select(_WorkspaceNode).where(_WorkspaceNode.id == node_id)
                )
                if node is None:
                    logger.warning("渲染后置启动：节点不存在 %s", pending["node_id"])
                    return

                await _render_agent.launch_render(
                    db=db,
                    auth_user=auth_user,
                    user_id=str(pending["user_id"]),
                    node=node,
                    project_name=str(pending.get("project_name") or ""),
                    requirement_text=str(pending.get("requirement_text") or ""),
                    direction_image_prompt=str(pending.get("direction_image_prompt") or None) or None,
                    industry=str(pending.get("industry") or "装备制造"),
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("渲染后置启动失败: %s", pending.get("node_id"))

    async def _launch_model_post_commit(
        self,
        *,
        auth_user: dict[str, object],
        pending: dict[str, object],
    ) -> None:
        """在 turn 提交后，用独立 DB 会话启动 3D 建模工作流。"""
        from uuid import UUID as _UUID

        from app.db.session import SessionLocal as _SessionLocal
        from app.models.workspace_node import WorkspaceNode as _WorkspaceNode
        from app.services.agents.model_agent import model_agent as _model_agent

        try:
            db = _SessionLocal()
            try:
                node_id = _UUID(str(pending["node_id"]))
                node = db.scalar(
                    select(_WorkspaceNode).where(_WorkspaceNode.id == node_id)
                )
                if node is None:
                    logger.warning("3D 后置启动：节点不存在 %s", pending["node_id"])
                    return

                await _model_agent.launch_3d(
                    db=db,
                    auth_user=auth_user,
                    user_id=str(pending["user_id"]),
                    node=node,
                    project_name=str(pending.get("project_name") or ""),
                    requirement_text=str(pending.get("requirement_text") or ""),
                    industry=str(pending.get("industry") or "装备制造"),
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("3D 后置启动失败: %s", pending.get("node_id"))

    async def _launch_cad_post_commit(
        self,
        *,
        auth_user: dict[str, object],
        pending: dict[str, object],
    ) -> None:
        """在 turn 提交后，用独立 DB 会话启动 CAD 工作流。"""
        from uuid import UUID as _UUID

        from app.db.session import SessionLocal as _SessionLocal
        from app.models.workspace_node import WorkspaceNode as _WorkspaceNode
        from app.services.agents.cad_agent import cad_agent as _cad_agent

        try:
            db = _SessionLocal()
            try:
                node_id = _UUID(str(pending["node_id"]))
                node = db.scalar(
                    select(_WorkspaceNode).where(_WorkspaceNode.id == node_id)
                )
                if node is None:
                    logger.warning("CAD 后置启动：节点不存在 %s", pending["node_id"])
                    return

                await _cad_agent.launch_cad(
                    db=db,
                    auth_user=auth_user,
                    user_id=str(pending["user_id"]),
                    node=node,
                    project_name=str(pending.get("project_name") or ""),
                    requirement_text=str(pending.get("requirement_text") or ""),
                    industry=str(pending.get("industry") or "装备制造"),
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("CAD 后置启动失败: %s", pending.get("node_id"))


workspace_turn_service = WorkspaceTurnService()
