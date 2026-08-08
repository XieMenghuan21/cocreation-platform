"""稳定 GptWorkspace → Workspace Graph 的非阻塞镜像输入。

镜像层只负责把已经成功发生的旧工作流结果同步成 WorkspaceNode，
不负责调度 Agent，也不能反向阻塞旧工作流。
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class WorkspaceMirrorRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_key: str = Field(alias="sourceKey", min_length=1, max_length=255)
    node_type: str = Field(alias="type", min_length=1, max_length=64)
    status: str = Field(default="completed", max_length=64)
    title: str = Field(default="", max_length=255)
    summary: str = ""
    project_id: str | None = Field(default=None, alias="projectId")
    task_id: str | None = Field(default=None, alias="taskId")
    version_id: str | None = Field(default=None, alias="versionId")
    parent_source_key: str | None = Field(default=None, alias="parentSourceKey")
    input_data: dict[str, Any] = Field(default_factory=dict, alias="inputData")
    output_data: dict[str, Any] = Field(default_factory=dict, alias="outputData")
    ui_data: dict[str, Any] = Field(default_factory=dict, alias="uiData")
