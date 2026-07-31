"""工作区状态接口 Schema。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def to_camel(field_name: str) -> str:
    first, *rest = field_name.split("_")
    return first + "".join(word.capitalize() for word in rest)


class WorkspaceFields(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )

    selected_project_id: str | None = Field(default=None, max_length=160)
    selected_reference_version_id: str | None = Field(default=None, max_length=160)
    selected_reference_asset_id: UUID | None = None
    active_scenario: str = Field(default="", max_length=120)
    active_workflow_stage: str = Field(default="", max_length=120)
    active_step_index: int = Field(default=0, ge=0)
    view_mode: str = Field(default="", max_length=64)
    scene_mode: str = Field(default="", max_length=64)
    selected_industry: str = Field(default="", max_length=120)
    generation_prompt: str = Field(default="", max_length=100_000)
    state_data: dict[str, object] = Field(default_factory=dict)


class WorkspaceUpdate(WorkspaceFields):
    version: int = Field(ge=0)


class WorkspaceReferenceUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    project_id: str | None = Field(default=None, alias="projectId", max_length=160)
    version_id: str = Field(alias="versionId", min_length=1, max_length=120)


class WorkspaceResponse(WorkspaceFields):
    version: int
    created_at: datetime | None
    updated_at: datetime | None
