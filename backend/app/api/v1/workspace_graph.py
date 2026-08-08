"""Workspace Graph API：Turn 入口 + Workspace 快照。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.core.identity import auth_user_id
from app.db.session import get_db
from app.models.conversation import Conversation
from app.schemas.workspace_graph import TurnRequest, TurnResponse, WorkspaceSnapshotData
from app.services.workspace_graph_service import workspace_graph_service
from app.services.workspace_turn_service import WorkspaceTurnError, workspace_turn_service
from app.services.agents.render_agent import sync_node_from_task
from app.utils.response import error_response, success_response

router = APIRouter(prefix="/conversations")


@router.post("/turns", response_model=dict, summary="新会话第一轮")
async def start_turn(
    request: TurnRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """不指定会话，自动创建 Conversation 并处理第一轮。"""
    user_id = auth_user_id(auth_user)
    try:
        result: TurnResponse = await workspace_turn_service.handle_turn(
            db,
            auth_user=auth_user,
            user_id=user_id,
            conversation_id=None,
            request=request,
        )
    except WorkspaceTurnError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail=error_response(message=exc.message, code=exc.status_code),
        ) from exc
    return success_response(
        data=result.model_dump(by_alias=True),
        message="本轮已处理",
    )


@router.post("/{conversation_id}/turns", response_model=dict, summary="追加一轮对话")
async def append_turn(
    conversation_id: UUID,
    request: TurnRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = auth_user_id(auth_user)
    try:
        result: TurnResponse = await workspace_turn_service.handle_turn(
            db,
            auth_user=auth_user,
            user_id=user_id,
            conversation_id=conversation_id,
            request=request,
        )
    except WorkspaceTurnError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail=error_response(message=exc.message, code=exc.status_code),
        ) from exc
    return success_response(
        data=result.model_dump(by_alias=True),
        message="本轮已处理",
    )


@router.get("/{conversation_id}/workspace", response_model=dict, summary="Workspace 快照")
def get_workspace(
    conversation_id: UUID,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user_id = auth_user_id(auth_user)
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    nodes = workspace_graph_service.get_nodes(
        db, user_id=user_id, conversation_id=conversation_id
    )
    for node in nodes:
        if node.task_id and node.status in {"queued", "running"}:
            sync_node_from_task(db, node.task_id)
    db.commit()
    active_tasks = [
        {
            "taskId": n.task_id,
            "nodeId": str(n.id),
            "nodeType": n.node_type,
            "status": n.status,
        }
        for n in nodes
        if n.task_id and n.status in {"queued", "running"}
    ]
    snapshot = WorkspaceSnapshotData(
        conversation={
            "id": str(conversation.id),
            "projectId": conversation.project_id,
            "title": conversation.title,
        },
        nodes=[workspace_graph_service.to_data(n) for n in nodes],
        node_assets={
            str(n.id): [
                {"id": a.id, "assetId": str(a.asset_id), "role": a.role}
                for a in n.assets
            ]
            for n in nodes
        },
        active_tasks=active_tasks,
        ui_state={
            "activeNodeId": (
                str(nodes[-1].id) if nodes and nodes[-1].status == "waiting_user" else None
            )
        },
    )
    return success_response(
        data=snapshot.model_dump(by_alias=True),
        message="Workspace 快照读取成功",
    )
