"""Database-backed orchestration runtime."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.orchestration import AgentArtifactLink, AgentRun, AgentRunEvent, WorkflowInstance
from app.services.orchestration.contracts import (
    AgentExecutionResult,
    AgentRunStatus,
    AgentType,
    JsonValue,
)


class OrchestrationRuntime:
    """Creates and updates persisted workflow and agent state."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_workflow(
        self,
        *,
        user_id: str,
        project_id: str,
        conversation_id: UUID | None,
        initial_input: dict[str, JsonValue],
    ) -> WorkflowInstance:
        workflow = WorkflowInstance(
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            status="queued",
            input_snapshot=initial_input,
            output_snapshot={},
        )
        self._db.add(workflow)
        self._db.flush()
        return workflow

    def enqueue_agent(
        self,
        *,
        workflow_id: str,
        agent_type: AgentType | str,
        input_snapshot: dict[str, JsonValue],
    ) -> AgentRun:
        workflow = self._load_workflow(workflow_id)
        run = AgentRun(
            workflow_id=workflow.id,
            user_id=workflow.user_id,
            project_id=workflow.project_id,
            conversation_id=workflow.conversation_id,
            agent_type=str(agent_type),
            status="queued",
            input_snapshot=input_snapshot,
            output_snapshot={},
        )
        self._db.add(run)
        self._db.flush()
        self._append_event(run, "queued", "queued", 0, "Agent queued", {})
        return run

    def mark_running(self, run: AgentRun) -> None:
        run.status = "running"
        run.started_at = run.started_at or _utcnow()
        self._append_event(run, "status", "running", 5, "Agent running", {})
        self._db.flush()

    def mark_succeeded(self, run: AgentRun, result: AgentExecutionResult) -> None:
        run.status = "succeeded"
        run.output_snapshot = dict(result.output_snapshot)
        run.error_code = None
        run.error_message = None
        run.completed_at = _utcnow()
        for asset_id in result.artifact_ids:
            self._db.add(
                AgentArtifactLink(
                    workflow_id=run.workflow_id,
                    agent_run_id=run.id,
                    asset_id=UUID(asset_id),
                    user_id=run.user_id,
                    project_id=run.project_id,
                    role="output",
                )
            )
        self._append_event(
            run,
            "status",
            "succeeded",
            100,
            result.message or "Agent succeeded",
            {"nextAgents": list(result.next_agents)},
        )
        self._db.flush()

    def mark_waiting_user(self, run: AgentRun, result: AgentExecutionResult) -> None:
        run.status = "waiting_user"
        run.output_snapshot = dict(result.output_snapshot)
        self._append_event(
            run,
            "status",
            "waiting_user",
            80,
            result.message or "Waiting for user confirmation",
            {"nextAgents": list(result.next_agents)},
        )
        self._db.flush()

    def mark_failed(self, run: AgentRun, *, error_code: str, error_message: str) -> None:
        run.status = "failed"
        run.error_code = error_code
        run.error_message = error_message
        run.completed_at = _utcnow()
        self._append_event(
            run,
            "error",
            "failed",
            100,
            error_message,
            {"errorCode": error_code},
        )
        self._db.flush()

    def retry_agent(self, agent_run_id: str) -> AgentRun:
        previous = self._load_agent_run(agent_run_id)
        retry = AgentRun(
            workflow_id=previous.workflow_id,
            user_id=previous.user_id,
            project_id=previous.project_id,
            conversation_id=previous.conversation_id,
            agent_type=previous.agent_type,
            status="queued",
            input_snapshot=dict(previous.input_snapshot),
            output_snapshot={},
            retry_count=previous.retry_count + 1,
        )
        self._db.add(retry)
        self._db.flush()
        self._append_event(
            retry,
            "retry",
            "queued",
            0,
            "Agent retry queued",
            {"previousAgentRunId": str(previous.id)},
        )
        return retry

    def _load_workflow(self, workflow_id: str) -> WorkflowInstance:
        workflow = self._db.get(WorkflowInstance, UUID(workflow_id))
        if workflow is None:
            raise ValueError("workflow not found")
        return workflow

    def _load_agent_run(self, agent_run_id: str) -> AgentRun:
        run = self._db.get(AgentRun, UUID(agent_run_id))
        if run is None:
            raise ValueError("agent run not found")
        return run

    def _append_event(
        self,
        run: AgentRun,
        event_type: str,
        status: AgentRunStatus | str,
        progress: int,
        message: str,
        event_data: dict[str, JsonValue],
    ) -> AgentRunEvent:
        max_sequence = self._db.scalar(
            select(func.max(AgentRunEvent.sequence)).where(AgentRunEvent.agent_run_id == run.id)
        )
        event = AgentRunEvent(
            workflow_id=run.workflow_id,
            agent_run_id=run.id,
            user_id=run.user_id,
            project_id=run.project_id,
            sequence=(max_sequence or 0) + 1,
            event_type=event_type,
            status=str(status),
            progress=progress,
            message=message,
            event_data=event_data,
        )
        self._db.add(event)
        return event


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
