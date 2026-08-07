"""Conversation workspace orchestration API."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_auth
from app.core.identity import auth_user_id
from app.db.session import get_db
from app.models.orchestration import AgentRun, WorkflowInstance
from app.schemas.orchestration import (
    OrchestrationActionRequest,
    OrchestrationStartRequest,
    WorkflowResponse,
)
from app.services.orchestration.contracts import AgentExecutionResult
from app.services.orchestration.runtime import OrchestrationRuntime

router = APIRouter(prefix="/orchestrations")


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
def create_orchestration(
    payload: OrchestrationStartRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    user_id = auth_user_id(auth_user)
    runtime = OrchestrationRuntime(db)
    workflow = runtime.create_workflow(
        user_id=user_id,
        project_id=payload.project_id,
        conversation_id=payload.conversation_id,
        initial_input={
            "prompt": payload.prompt,
            "attachmentAssetIds": [str(asset_id) for asset_id in payload.attachment_asset_ids],
        },
    )
    runtime.enqueue_agent(
        workflow_id=str(workflow.id),
        agent_type="requirement",
        input_snapshot=dict(workflow.input_snapshot),
    )
    db.commit()
    return _load_workflow_response(db, workflow.id, user_id)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
def get_orchestration(
    workflow_id: UUID,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    return _load_workflow_response(db, workflow_id, auth_user_id(auth_user))


@router.post("/{workflow_id}/actions", response_model=WorkflowResponse)
def apply_orchestration_action(
    workflow_id: UUID,
    payload: OrchestrationActionRequest,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    user_id = auth_user_id(auth_user)
    workflow = _load_workflow(db, workflow_id, user_id)
    if payload.type != "confirm_design_direction":
        raise HTTPException(status_code=422, detail="不支持的工作流动作")
    runtime = OrchestrationRuntime(db)
    direction_payload = {"confirmedDirection": payload.payload}
    render = runtime.enqueue_agent(
        workflow_id=str(workflow.id),
        agent_type="render",
        input_snapshot=direction_payload,
    )
    three_d = runtime.enqueue_agent(
        workflow_id=str(workflow.id),
        agent_type="three_d",
        input_snapshot=direction_payload,
    )
    runtime.mark_running(render)
    runtime.mark_running(three_d)
    runtime.mark_waiting_user(
        render,
        AgentExecutionResult(
            status="waiting_user",
            output_snapshot={"message": "Render agent queued for ComfyUI reference edit"},
            artifact_ids=(),
            next_agents=(),
        ),
    )
    runtime.mark_waiting_user(
        three_d,
        AgentExecutionResult(
            status="waiting_user",
            output_snapshot={"message": "3D agent queued"},
            artifact_ids=(),
            next_agents=("cad",),
        ),
    )
    db.commit()
    return _load_workflow_response(db, workflow.id, user_id)


@router.post("/{workflow_id}/agent-runs/{agent_run_id}/retry", response_model=WorkflowResponse)
def retry_agent_run(
    workflow_id: UUID,
    agent_run_id: UUID,
    auth_user: dict[str, object] = Depends(require_auth),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    user_id = auth_user_id(auth_user)
    workflow = _load_workflow(db, workflow_id, user_id)
    run = db.scalar(
        select(AgentRun).where(
            AgentRun.id == agent_run_id,
            AgentRun.workflow_id == workflow.id,
            AgentRun.user_id == user_id,
            AgentRun.project_id == workflow.project_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Agent 运行不存在")
    if run.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败的 Agent 可以重试")
    runtime = OrchestrationRuntime(db)
    runtime.retry_agent(str(run.id))
    db.commit()
    return _load_workflow_response(db, workflow.id, user_id)


def _load_workflow(db: Session, workflow_id: UUID, user_id: str) -> WorkflowInstance:
    workflow = db.scalar(
        select(WorkflowInstance)
        .options(selectinload(WorkflowInstance.agent_runs).selectinload(AgentRun.events))
        .where(WorkflowInstance.id == workflow_id, WorkflowInstance.user_id == user_id)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return workflow


def _load_workflow_response(db: Session, workflow_id: UUID, user_id: str) -> WorkflowResponse:
    db.expire_all()
    workflow = _load_workflow(db, workflow_id, user_id)
    return WorkflowResponse.model_validate(workflow).model_copy(
        update={"agent_runs": list(workflow.agent_runs)}
    )
