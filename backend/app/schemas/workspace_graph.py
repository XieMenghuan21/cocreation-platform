"""Workspace Graph 接口 schema。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceNodeAssetData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    asset_id: UUID = Field(alias="assetId")
    role: str
    created_at: datetime = Field(alias="createdAt")


class WorkspaceNodeData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    conversation_id: UUID = Field(alias="conversationId")
    project_id: str | None = Field(default=None, alias="projectId")
    parent_id: UUID | None = Field(default=None, alias="parentId")
    branch_id: UUID | None = Field(default=None, alias="branchId")
    node_type: str = Field(alias="type")
    status: str
    title: str
    summary: str
    agent_key: str | None = Field(default=None, alias="agentKey")
    task_id: str | None = Field(default=None, alias="taskId")
    version_id: str | None = Field(default=None, alias="versionId")
    input_data: dict[str, Any] = Field(default_factory=dict, alias="inputData")
    output_data: dict[str, Any] = Field(default_factory=dict, alias="outputData")
    ui_data: dict[str, Any] = Field(default_factory=dict, alias="uiData")
    assets: list[WorkspaceNodeAssetData] = Field(default_factory=list)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class TurnRequest(BaseModel):
    """一轮用户输入：文本 / 资产引用 / 结构化卡片动作。"""

    model_config = ConfigDict(populate_by_name=True)

    text: str | None = Field(default=None, max_length=20000)
    asset_ids: list[str] = Field(default_factory=list, alias="assetIds")
    action: dict[str, Any] | None = Field(default=None)


class TurnResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_id: UUID = Field(alias="conversationId")
    message: dict[str, Any]
    nodes_created: list[WorkspaceNodeData] = Field(
        default_factory=list, alias="nodesCreated"
    )
    nodes_updated: list[WorkspaceNodeData] = Field(
        default_factory=list, alias="nodesUpdated"
    )
    tasks_started: list[dict[str, Any]] = Field(
        default_factory=list, alias="tasksStarted"
    )
    workspace: dict[str, Any] = Field(
        default_factory=lambda: {
            "activeNodeId": None,
            "previewNodeId": None,
        }
    )


class WorkspaceSnapshotData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    nodes: list[WorkspaceNodeData] = Field(default_factory=list)
    node_assets: dict[str, list[WorkspaceNodeAssetData]] = Field(
        default_factory=dict, alias="nodeAssets"
    )
    active_tasks: list[dict[str, Any]] = Field(default_factory=list, alias="activeTasks")
    ui_state: dict[str, Any] = Field(default_factory=dict, alias="uiState")
