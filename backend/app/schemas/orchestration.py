"""Pydantic schemas for workspace orchestration APIs."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class OrchestrationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_id: str = Field(alias="projectId", min_length=1, max_length=160)
    conversation_id: UUID | None = Field(default=None, alias="conversationId")
    prompt: str = Field(min_length=1, max_length=20000)
    attachment_asset_ids: list[UUID] = Field(default_factory=list, alias="attachmentAssetIds")


class OrchestrationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=120)
    payload: dict[str, object] = Field(default_factory=dict)


class AgentRunEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    agent_run_id: UUID = Field(alias="agentRunId")
    sequence: int
    event_type: str = Field(alias="eventType")
    status: str
    progress: int
    message: str
    event_data: dict[str, object] = Field(alias="eventData")
    created_at: datetime = Field(alias="createdAt")


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    workflow_id: UUID = Field(alias="workflowId")
    agent_type: str = Field(alias="agentType")
    status: str
    input_snapshot: dict[str, object] = Field(alias="inputSnapshot")
    output_snapshot: dict[str, object] = Field(alias="outputSnapshot")
    error_code: str | None = Field(alias="errorCode")
    error_message: str | None = Field(alias="errorMessage")
    retry_count: int = Field(alias="retryCount")
    created_at: datetime = Field(alias="createdAt")
    started_at: datetime | None = Field(alias="startedAt")
    completed_at: datetime | None = Field(alias="completedAt")
    events: list[AgentRunEventResponse] = Field(default_factory=list)


class WorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    project_id: str = Field(alias="projectId")
    conversation_id: UUID | None = Field(alias="conversationId")
    status: str
    input_snapshot: dict[str, object] = Field(alias="inputSnapshot")
    output_snapshot: dict[str, object] = Field(alias="outputSnapshot")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    completed_at: datetime | None = Field(alias="completedAt")
    agent_runs: list[AgentRunResponse] = Field(alias="agentRuns")
