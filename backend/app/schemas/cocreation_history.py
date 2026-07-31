"""共创项目历史接口 schema。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.json_shape import validate_json_shape


class ProjectRecordPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., max_length=160)
    name: str = Field(..., max_length=255)
    industry: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=20000)
    input_mode: str = Field(default="prompt", alias="inputMode", max_length=64)
    created_at: str = Field(..., alias="createdAt", max_length=64)
    updated_at: str = Field(..., alias="updatedAt", max_length=64)
    last_task_id: str | None = Field(default=None, alias="lastTaskId", max_length=160)
    last_status: str | None = Field(default=None, alias="lastStatus", max_length=120)
    last_result_text: str | None = Field(default=None, alias="lastResultText", max_length=20000)
    last_image_url: str | None = Field(default=None, alias="lastImageUrl", max_length=20000)
    version_count: int | None = Field(default=None, alias="versionCount")


class GeneratedAssetPayload(BaseModel):
    asset_id: str | None = Field(default=None, alias="assetId", min_length=36, max_length=36)
    kind: str | None = Field(default=None, max_length=64)


class VersionSnapshotFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., max_length=120)
    label: str = Field(..., max_length=255)
    status: str = Field(..., max_length=120)
    note: str = Field(..., max_length=20000)
    project_id: str | None = Field(default=None, alias="projectId", max_length=160)
    project_name: str | None = Field(default=None, alias="projectName", max_length=255)
    version_number: int | None = Field(default=None, alias="versionNumber")
    is_finalized: bool | None = Field(default=None, alias="isFinalized")
    source_project_id: str | None = Field(default=None, alias="sourceProjectId", max_length=160)
    prompt: str | None = Field(default=None, max_length=20000)
    optimized_prompt: str | None = Field(default=None, alias="optimizedPrompt", max_length=20000)
    result_text: str | None = Field(default=None, alias="resultText", max_length=20000)
    preview_image_url: str | None = Field(default=None, alias="previewImageUrl", max_length=20000)
    generated_image_urls: list[str] = Field(default_factory=list, alias="generatedImageUrls", max_length=50)
    change_type: str | None = Field(default=None, alias="changeType", max_length=160)
    source_object: str | None = Field(default=None, alias="sourceObject", max_length=255)
    task_id: str | None = Field(default=None, alias="taskId", max_length=160)
    script_asset_id: str | None = Field(default=None, alias="scriptAssetId", max_length=36)
    output_asset_id: str | None = Field(default=None, alias="outputAssetId", max_length=36)
    download_url: str | None = Field(default=None, alias="downloadUrl", max_length=20000)
    execution_summary: str | None = Field(default=None, alias="executionSummary", max_length=20000)
    created_at: str | None = Field(default=None, alias="createdAt", max_length=64)
    cli_executed: bool | None = Field(default=None, alias="cliExecuted")
    export_format: str | None = Field(default=None, alias="exportFormat", max_length=64)
    model_objects: list[dict[str, object]] = Field(default_factory=list, alias="modelObjects", max_length=200)
    parameters: list[dict[str, object]] = Field(default_factory=list, max_length=200)
    generated_assets: list[GeneratedAssetPayload] = Field(
        default_factory=list,
        alias="generatedAssets",
        max_length=200,
    )
    diagnostics: list[dict[str, object]] = Field(default_factory=list, max_length=200)

    @field_validator("generated_image_urls")
    @classmethod
    def validate_image_urls(cls, value: list[str]) -> list[str]:
        if any(len(item) > 2048 for item in value):
            raise ValueError("图片 URL 长度超过限制")
        return value

    @field_validator(
        "model_objects",
        "parameters",
        "diagnostics",
    )
    @classmethod
    def validate_metadata_shape(
        cls,
        value: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        validate_json_shape(value)
        return value


class VersionSnapshotPayload(VersionSnapshotFields):
    script_path: str | None = Field(
        default=None, alias="scriptPath", max_length=20000, exclude=True
    )
    work_dir: str | None = Field(
        default=None, alias="workDir", max_length=20000, exclude=True
    )
    output_path: str | None = Field(
        default=None, alias="outputPath", max_length=20000, exclude=True
    )

    @model_validator(mode="after")
    def reject_forged_publication(self) -> "VersionSnapshotPayload":
        if self.status in {"published", "已发布"} or self.is_finalized is True:
            raise ValueError("发布状态只能通过发布接口设置")
        return self

    @model_validator(mode="after")
    def reject_duplicate_generated_assets(self) -> "VersionSnapshotPayload":
        asset_ids = [asset.asset_id for asset in self.generated_assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("generatedAssets 不能包含重复资产")
        return self


class UpsertProjectVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRecordPayload
    version: VersionSnapshotPayload


class GeneratedAssetResponse(GeneratedAssetPayload):
    download_url: str = Field(alias="downloadUrl")


class VersionSnapshotResponse(VersionSnapshotFields):
    generated_assets: list[GeneratedAssetResponse] = Field(
        default_factory=list,
        alias="generatedAssets",
    )


class HistoryListData(BaseModel):
    projects: list[ProjectRecordPayload]
    snapshots: list[VersionSnapshotResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class HistoryWriteData(BaseModel):
    project_id: str = Field(alias="projectId")
    version_id: str = Field(alias="versionId")


class HistoryPublishData(HistoryWriteData):
    published: bool
    asset_count: int = Field(alias="assetCount")


class HistoryDeleteData(BaseModel):
    deleted: bool


class HistoryResponseBase(BaseModel):
    code: int
    message: str
    success: bool
    error_code: str | None = Field(alias="errorCode")
    request_id: str | None = Field(alias="requestId")


class HistoryListResponse(HistoryResponseBase):
    data: HistoryListData


class HistoryWriteResponse(HistoryResponseBase):
    data: HistoryWriteData


class HistoryPublishResponse(HistoryResponseBase):
    data: HistoryPublishData


class HistoryDeleteResponse(HistoryResponseBase):
    data: HistoryDeleteData
